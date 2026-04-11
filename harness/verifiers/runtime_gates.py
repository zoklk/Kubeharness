"""
런타임 게이트 함수. LLM 없음, 순수 결정적 실행.

run_runtime_phase1(service_name) -> {"passed": bool, "checks": [...]}

배포 경로 자동 감지:
  has_helm      : edge-server/helm/<service>/  → helm upgrade --install → kubectl wait pods
  has_manifests : edge-server/manifests/<service>/ → kubectl apply (pod wait 없음)
  둘 다 없음    : 즉시 fail

컨벤션 (context/inject/conventions.md와 동기화):
  namespace      : gikview
  release_name   : <service>-dev-v1
  label_selector : app.kubernetes.io/name=<service>
  smoke_test     : edge-server/scripts/smoke-test-<service>.sh (없으면 skip)

events 조회는 Phase 2 LLM이 kagent로 직접 수행. Phase 1에서는 하지 않음.
"""

import json
import yaml
from pathlib import Path
from typing import Optional

from harness.config import NAMESPACE, PROJECT_ROOT, label_selector, release_name
from harness.tools import helm, kubectl, shell

_BUILD_CONFIG_PATH = PROJECT_ROOT / "config" / "build.yaml"

# pod가 이 상태이면 기다려도 복구 불가 → 조기 종료
_TERMINAL_STATES = frozenset({
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
    "Error", "OOMKilled", "InvalidImageName", "CreateContainerConfigError",
})


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _result(name: str, status: str, detail: str,
            log_dir: Optional[str] = None, raw: str = "") -> dict:
    log_path = None
    if log_dir:
        p = Path(log_dir) / f"{name}.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(raw or detail, encoding="utf-8")
        log_path = str(p)
    return {"name": name, "status": status, "detail": detail, "log_path": log_path}


def _skip(name: str) -> dict:
    return {"name": name, "status": "skip", "detail": "prior step failed", "log_path": None}


def _from_run(name: str, r: dict, log_dir: Optional[str]) -> dict:
    status = "pass" if r["exit_code"] == 0 else "fail"
    detail = (r["stderr"] or r["stdout"]).strip() or "OK"
    return _result(name, status, detail, log_dir, r["stdout"] + r["stderr"])


# ── 빌드 설정 ─────────────────────────────────────────────────────────────────

def _load_build_config() -> dict:
    if _BUILD_CONFIG_PATH.exists():
        return yaml.safe_load(_BUILD_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {}


# ── Docker 빌드 ────────────────────────────────────────────────────────────────

def _run_docker_build(
    service_name: str,
    registry: str,
    image_tag: str,
    log_dir: Optional[str] = None,
) -> list[dict]:
    """
    Dockerfile이 있을 때 build + push 실행.
    반환: [docker_build 체크, docker_push 체크] — 순서대로 실행, fail 시 이후 skip.
    Dockerfile 없으면 빈 리스트 반환 (skip 없음, 해당 서비스에 불필요한 스텝).
    """
    docker_dir = PROJECT_ROOT / f"edge-server/docker/{service_name}"
    if not (docker_dir / "Dockerfile").exists():
        return []

    tag = f"{registry}/{service_name}:{image_tag}"

    r = shell.run(["docker", "build", "-t", tag, str(docker_dir)])
    build_check = _from_run("docker_build", r, log_dir)
    if build_check["status"] == "fail":
        return [build_check, _skip("docker_push")]

    r = shell.run(["docker", "push", tag])
    push_check = _from_run("docker_push", r, log_dir)
    return [build_check, push_check]


# ── Terminal 상태 감지 ────────────────────────────────────────────────────────

def _is_terminal_failure(pods_r: dict) -> bool:
    """pod 중 하나라도 terminal 상태이면 True. kubectl get pods 실패 시 False."""
    if pods_r["exit_code"] != 0:
        return False
    try:
        items = json.loads(pods_r["stdout"]).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        return False
    if not items:
        return False
    for pod in items:
        for cs in pod.get("status", {}).get("containerStatuses", []):
            if cs.get("state", {}).get("waiting", {}).get("reason", "") in _TERMINAL_STATES:
                return True
            if cs.get("state", {}).get("terminated", {}).get("reason", "") in _TERMINAL_STATES:
                return True
    return False


def _terminal_detail(pods_r: dict) -> str:
    """terminal 상태 pod/container 요약 (최대 3건)."""
    try:
        items = json.loads(pods_r["stdout"]).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        return ""
    parts = []
    for pod in items:
        pname = pod.get("metadata", {}).get("name", "?")
        for cs in pod.get("status", {}).get("containerStatuses", []):
            waiting = cs.get("state", {}).get("waiting", {})
            reason = waiting.get("reason", "")
            if reason in _TERMINAL_STATES:
                msg = waiting.get("message", "")[:80]
                parts.append(f"{pname}: {reason}" + (f" ({msg})" if msg else ""))
            terminated = cs.get("state", {}).get("terminated", {})
            treason = terminated.get("reason", "")
            if treason in _TERMINAL_STATES:
                parts.append(f"{pname}: terminated/{treason}")
    return "; ".join(parts[:3])


# ── 메인 게이트 함수 ──────────────────────────────────────────────────────────

def run_runtime_phase1(service_name: str, log_dir: Optional[str] = None) -> dict:
    """
    순서대로 실행. 하나 fail이면 이후 체크는 skip하고 즉시 반환.

    배포 유형 자동 감지:
      - edge-server/helm/<service>/ 존재 → helm upgrade --install + kubectl wait pods
      - edge-server/manifests/<service>/ 존재 (helm 없음) → kubectl apply (pod wait 생략)
      - 둘 다 없음 → 즉시 fail

    Phase 1 체크: deploy → kubectl_wait (helm만) → smoke_test
    events 조회는 Phase 2 LLM(kagent)이 담당. Phase 1에서는 수행하지 않음.

    Returns:
        {"passed": bool, "checks": [{"name", "status", "detail", "log_path"}, ...]}
    """
    from harness.config import cluster_config

    chart_path = str(PROJECT_ROOT / f"edge-server/helm/{service_name}")
    manifest_dir = str(PROJECT_ROOT / f"edge-server/manifests/{service_name}")
    rname = release_name(service_name)
    lsel = label_selector(service_name)
    smoke_test_path = PROJECT_ROOT / f"edge-server/scripts/smoke-test-{service_name}.sh"

    has_helm = Path(chart_path).is_dir()
    has_manifests = Path(manifest_dir).is_dir()

    checks = []

    # ① 배포 아티팩트 없음 → 즉시 실패
    if not has_helm and not has_manifests:
        checks.append(_result(
            "deploy", "fail",
            f"no helm chart at 'edge-server/helm/{service_name}' or "
            f"manifests at 'edge-server/manifests/{service_name}'",
            log_dir,
        ))
        return {"passed": False, "checks": checks}

    # ② docker build + push (Dockerfile 존재 시)
    build_cfg = _load_build_config()
    registry = build_cfg.get("registry", "")
    image_tag = build_cfg.get("image_tag", "dev")

    deploy_step = "helm_install" if has_helm else "kubectl_apply"
    post_deploy_skips = (
        [_skip("kubectl_wait"), _skip("smoke_test")]
        if has_helm
        else [_skip("smoke_test")]
    )

    if not registry and (PROJECT_ROOT / f"edge-server/docker/{service_name}" / "Dockerfile").exists():
        checks.append(_result(
            "docker_build", "fail",
            "config/build.yaml missing or 'registry' not set",
            log_dir,
        ))
        checks += [_skip("docker_push"), _skip(deploy_step)] + post_deploy_skips
        return {"passed": False, "checks": checks}

    docker_checks = _run_docker_build(service_name, registry, image_tag, log_dir)
    checks.extend(docker_checks)
    if docker_checks and docker_checks[-1]["status"] == "fail":
        checks += [_skip(deploy_step)] + post_deploy_skips
        return {"passed": False, "checks": checks}

    # ③ 배포
    if has_helm:
        active = cluster_config().get("_active", "dev")
        values_files = [
            str(PROJECT_ROOT / f"edge-server/helm/{service_name}/{vf}")
            for vf in ["values.yaml", f"values-{active}.yaml"]
            if (PROJECT_ROOT / f"edge-server/helm/{service_name}/{vf}").exists()
        ]

        # terminal 상태 pod만 선제 삭제: CrashLoopBackOff back-off 축적 pod가 rolling update를 막는 것을 방지
        # 정상 Running pod를 강제 삭제하면 EMQX 등 agent mode 전환 재시작 사이클이 반복됨
        _pods_before = kubectl.get_pods(NAMESPACE, label=lsel)
        if _is_terminal_failure(_pods_before):
            kubectl.delete_pods(NAMESPACE, lsel)

        # immutable field 감지 시 uninstall 후 재설치
        # "immutable": generic k8s field, "forbidden: updates to statefulset spec": PVC/volumeClaimTemplates 변경
        r = helm.upgrade_install(rname, chart_path, NAMESPACE, values_files)
        output_lower = (r["stderr"] + r["stdout"]).lower()
        if r["exit_code"] != 0 and (
            "immutable" in output_lower
            or ("forbidden" in output_lower and "statefulset spec" in output_lower)
        ):
            uninstall_r = helm.uninstall(rname, NAMESPACE)
            uninstall_check = _from_run("helm_uninstall_immutable", uninstall_r, log_dir)
            checks.append(uninstall_check)
            if uninstall_check["status"] == "fail":
                checks += [_skip("helm_install"), _skip("kubectl_wait"), _skip("smoke_test")]
                return {"passed": False, "checks": checks}
            r = helm.upgrade_install(rname, chart_path, NAMESPACE, values_files)

        checks.append(_from_run("helm_install", r, log_dir))
        if checks[-1]["status"] == "fail":
            checks += [_skip("kubectl_wait"), _skip("smoke_test")]
            return {"passed": False, "checks": checks}

        # ④ kubectl wait pods — 2단계: 60s 조기 감지 + 240s 잔여 대기
        # 60s 후 terminal 상태(CrashLoopBackOff 등)면 즉시 fail (300s 낭비 방지)
        # 아직 기동 중(Pending/Init)이면 잔여 240s 대기
        r60 = kubectl.wait("pods", "Ready", NAMESPACE, label=lsel, timeout="60s")
        if r60["exit_code"] == 0:
            checks.append(_from_run("kubectl_wait", r60, log_dir))
        else:
            pods_r = kubectl.get_pods(NAMESPACE, label=lsel)
            if _is_terminal_failure(pods_r):
                detail = _terminal_detail(pods_r) or (r60["stderr"] or r60["stdout"]).strip() or "terminal failure after 60s"
                checks.append(_result("kubectl_wait", "fail",
                                      f"early exit: {detail}",
                                      log_dir, r60["stdout"] + r60["stderr"]))
            else:
                r240 = kubectl.wait("pods", "Ready", NAMESPACE, label=lsel, timeout="240s")
                checks.append(_from_run("kubectl_wait", r240, log_dir))

        if checks[-1]["status"] == "fail":
            checks.append(_skip("smoke_test"))
            return {"passed": False, "checks": checks}

    else:
        # manifest-only (CRD, 클러스터 레벨 설정 등) — pod wait 생략
        r = kubectl.apply(manifest_dir, NAMESPACE)
        checks.append(_from_run("kubectl_apply", r, log_dir))
        if checks[-1]["status"] == "fail":
            checks += [_skip("smoke_test")]
            return {"passed": False, "checks": checks}

    # ⑤ smoke test
    if smoke_test_path.exists():
        r = shell.run(["bash", str(smoke_test_path)])
        checks.append(_from_run("smoke_test", r, log_dir))
        if checks[-1]["status"] == "fail":
            return {"passed": False, "checks": checks}
    else:
        checks.append(_result("smoke_test", "skip",
                               f"no smoke test at {smoke_test_path.relative_to(PROJECT_ROOT)}", log_dir))

    return {"passed": True, "checks": checks}
