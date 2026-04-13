"""
Static Verifier 노드. LLM 없음, 순수 결정적.

처리 순서:
  1. path_prefix 검사 (edge-server/ 이외 경로 차단)
  2. dev_artifacts에서 helm chart 식별
  3. 해당 유형에 맞는 정적 체크 실행 (각 체크는 독립 실행)
  4. state 업데이트: static_verification, verification, current_sub_goal.stage
"""

from pathlib import Path

from harness.config import ARTIFACT_PREFIX, NAMESPACE, PROJECT_ROOT, release_name
from harness.state import HarnessState
from harness.verifiers import check_result, node_log_dir, static, values_files


# ── artifacts 식별 ────────────────────────────────────────────────────────────

def _chart_path(service_name: str) -> str:
    return str(PROJECT_ROOT / f"{ARTIFACT_PREFIX}helm/{service_name}")


def _docker_dir(service_name: str) -> str:
    return str(PROJECT_ROOT / f"{ARTIFACT_PREFIX}docker/{service_name}")


def _has_helm(files: list[str], service_name: str) -> bool:
    prefix = f"{ARTIFACT_PREFIX}helm/{service_name}/"
    return any(f.startswith(prefix) for f in files)


def _has_docker(files: list[str], service_name: str) -> bool:
    prefix = f"{ARTIFACT_PREFIX}docker/{service_name}/"
    return any(f.startswith(prefix) for f in files)


def _has_ebpf(files: list[str]) -> bool:
    return any(f.startswith(f"{ARTIFACT_PREFIX}ebpf/") for f in files)


# ── 노드 함수 ──────────────────────────────────────────────────────────────────

def static_verifier_node(state: HarnessState) -> dict:
    sub_goal = state["current_sub_goal"]
    service_name = sub_goal.get("service_name") or sub_goal["name"]
    artifacts = state.get("dev_artifacts") or {}
    files: list[str] = artifacts.get("files", [])

    log_dir = node_log_dir(state, "static")

    checks = []

    # ① path prefix (항상 먼저 — 위반 시 이후 체크도 계속 실행)
    checks.append(static.check_path_prefix(files, log_dir=log_dir))

    # ② helm chart 체크
    if _has_helm(files, service_name):
        chart_path = _chart_path(service_name)
        rname = release_name(service_name)
        vf = values_files(chart_path)

        checks.append(static.check_yamllint(chart_path, log_dir=log_dir))
        checks.append(static.check_helm_lint(chart_path, vf, log_dir=log_dir))
        checks.append(static.check_helm_template_kubeconform(
            chart_path, rname, NAMESPACE, vf, log_dir=log_dir))
        checks.append(static.check_trivy_config(chart_path, log_dir=log_dir))
        checks.append(static.check_gitleaks(chart_path, log_dir=log_dir))
        checks.append(static.check_helm_dry_run_server(
            chart_path, rname, NAMESPACE, vf, log_dir=log_dir))

    # ③ custom image 체크 (Dockerfile)
    if _has_docker(files, service_name):
        checks.append(static.check_dockerfile(_docker_dir(service_name), log_dir=log_dir))
        checks.append(static.check_gitleaks(_docker_dir(service_name), log_dir=log_dir))

    # eBPF 소스 — 정적 도구 체크 없음 (빌드/연결은 사람이 직접 관리)
    #    artifact_detection 통과 목적으로만 감지

    # ④ 인식된 아티팩트가 없으면 fail
    if not any([
        _has_helm(files, service_name),
        _has_docker(files, service_name),
        _has_ebpf(files),
    ]):
        checks.append(check_result(
            "artifact_detection", "fail",
            f"No helm chart, Dockerfile, or eBPF source "
            f"found for service '{service_name}' in dev_artifacts",
            log_dir,
        ))

    passed = all(c["status"] in ("pass", "skip") for c in checks)

    return {
        "current_sub_goal": {**sub_goal, "stage": "static_verify"},
        "static_verification": {"passed": passed, "checks": checks},
        "verification": {
            "passed": passed,
            "stage": "static",
            "checks": checks,
            "log_dir": str(Path(log_dir).parent) + "/",  # .../attempt_N/
        },
    }
