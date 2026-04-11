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
"""

import json
import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from harness.config import NAMESPACE, PROJECT_ROOT, label_selector, release_name
from harness.tools import helm, kubectl, shell

_BUILD_CONFIG_PATH = PROJECT_ROOT / "config" / "build.yaml"


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


# ── 이벤트 파싱 ───────────────────────────────────────────────────────────────

def _parse_warning_events(r: dict, log_dir: Optional[str]) -> dict:
    """kubectl get events JSON에서 최근 5분 이내 Warning 이벤트만 추출."""
    raw = r["stdout"] + r["stderr"]

    if r["exit_code"] != 0:
        detail = raw.strip() or "kubectl get events failed"
        return _result("kubectl_events", "fail", detail, log_dir, raw)

    try:
        items = json.loads(r["stdout"]).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        return _result("kubectl_events", "fail", "events JSON parse error", log_dir, raw)

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    warnings = []
    for ev in items:
        ts_str = (
            ev.get("lastTimestamp")
            or ev.get("eventTime")
            or (ev.get("series") or {}).get("lastObservedTime")
        )
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts >= cutoff:
            obj = ev.get("involvedObject", {}).get("name", "?")
            reason = ev.get("reason", "")
            msg = ev.get("message", "")
            warnings.append(f"[{obj}] {reason}: {msg}")

    if warnings:
        detail = f"{len(warnings)} warning(s) in last 5m: " + "; ".join(warnings[:5])
        return _result("kubectl_events", "fail", detail, log_dir, raw)

    return _result("kubectl_events", "pass",
                   f"no warnings in last 5m (total={len(items)})", log_dir, raw)


# ── 메인 게이트 함수 ──────────────────────────────────────────────────────────

def run_runtime_phase1(service_name: str, log_dir: Optional[str] = None) -> dict:
    """
    순서대로 실행. 하나 fail이면 이후 체크는 skip하고 즉시 반환.

    배포 유형 자동 감지:
      - edge-server/helm/<service>/ 존재 → helm upgrade --install + kubectl wait pods
      - edge-server/manifests/<service>/ 존재 (helm 없음) → kubectl apply (pod wait 생략)
      - 둘 다 없음 → 즉시 fail

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
        [_skip("kubectl_wait"), _skip("kubectl_events"), _skip("smoke_test")]
        if has_helm
        else [_skip("kubectl_events"), _skip("smoke_test")]
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
                checks += [_skip("helm_install"), _skip("kubectl_wait"),
                           _skip("kubectl_events"), _skip("smoke_test")]
                return {"passed": False, "checks": checks}
            r = helm.upgrade_install(rname, chart_path, NAMESPACE, values_files)

        checks.append(_from_run("helm_install", r, log_dir))
        if checks[-1]["status"] == "fail":
            checks += [_skip("kubectl_wait"), _skip("kubectl_events"), _skip("smoke_test")]
            return {"passed": False, "checks": checks}

        # ④ kubectl wait pods (helm only — manifest/CRD는 pod 없음)
        # 300s: StatefulSet 이미지 풀 + 순차 기동 감안
        r = kubectl.wait("pods", "Ready", NAMESPACE, label=lsel, timeout="300s")
        checks.append(_from_run("kubectl_wait", r, log_dir))
        if checks[-1]["status"] == "fail":
            # kubectl_events는 진단 정보를 위해 계속 실행 (Phase 2 LLM에 전달)
            r = kubectl.get_events(NAMESPACE, field_selector="type=Warning,involvedObject.kind=Pod")
            checks.append(_parse_warning_events(r, log_dir))
            checks.append(_skip("smoke_test"))
            return {"passed": False, "checks": checks}

    else:
        # manifest-only (CRD, 클러스터 레벨 설정 등) — pod wait 생략
        r = kubectl.apply(manifest_dir, NAMESPACE)
        checks.append(_from_run("kubectl_apply", r, log_dir))
        if checks[-1]["status"] == "fail":
            checks += [_skip("kubectl_events"), _skip("smoke_test")]
            return {"passed": False, "checks": checks}

    # ⑤ kubectl get events (Warning, 최근 5분)
    r = kubectl.get_events(NAMESPACE, field_selector="type=Warning,involvedObject.kind=Pod")
    checks.append(_parse_warning_events(r, log_dir))
    if checks[-1]["status"] == "fail":
        checks.append(_skip("smoke_test"))
        return {"passed": False, "checks": checks}

    # ⑥ smoke test
    if smoke_test_path.exists():
        r = shell.run(["bash", str(smoke_test_path)])
        checks.append(_from_run("smoke_test", r, log_dir))
        if checks[-1]["status"] == "fail":
            return {"passed": False, "checks": checks}
    else:
        checks.append(_result("smoke_test", "skip",
                               f"no smoke test at {smoke_test_path.relative_to(PROJECT_ROOT)}", log_dir))

    return {"passed": True, "checks": checks}
