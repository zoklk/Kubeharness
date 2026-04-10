"""
Static Verifier 노드. LLM 없음, 순수 결정적.

처리 순서:
  1. path_prefix 검사 (edge-server/ 이외 경로 차단)
  2. dev_artifacts에서 helm chart / manifest 디렉토리 식별
  3. 해당 유형에 맞는 정적 체크 실행 (각 체크는 독립 실행)
  4. state 업데이트: static_verification, verification, current_sub_goal.stage
"""

from pathlib import Path
from typing import Optional

from harness.state import HarnessState
from harness.verifiers import static

NAMESPACE = "gikview"

# 프로젝트 루트: harness/nodes/ → harness/ → GikView/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ── artifacts 식별 ────────────────────────────────────────────────────────────

def _chart_path(service_name: str) -> str:
    return f"edge-server/helm/{service_name}"


def _manifest_dir(service_name: str) -> str:
    return f"edge-server/manifests/{service_name}"


def _values_files(chart_path: str) -> list[str]:
    """존재하는 values 파일만 반환. CWD 무관하게 PROJECT_ROOT 기준으로 확인."""
    return [
        vf for vf in [
            f"{chart_path}/values.yaml",
            f"{chart_path}/values-dev.yaml",
        ]
        if (PROJECT_ROOT / vf).exists()
    ]


def _has_helm(files: list[str], service_name: str) -> bool:
    prefix = f"{_chart_path(service_name)}/"
    return any(f.startswith(prefix) for f in files)


def _has_manifests(files: list[str], service_name: str) -> bool:
    prefix = f"{_manifest_dir(service_name)}/"
    return any(f.startswith(prefix) for f in files)


# ── 노드 함수 ──────────────────────────────────────────────────────────────────

def _log_dir(state: HarnessState) -> str:
    """phase/sub_goal/attempt_N 구조로 로그 경로 생성. 재시도 시에도 일관성 유지."""
    phase = state.get("current_phase", "unknown")
    name = state["current_sub_goal"]["name"]
    attempt = state.get("error_count", 0)
    return f"logs/raw/{phase}/{name}/attempt_{attempt}/static"


def static_verifier_node(state: HarnessState) -> dict:
    sub_goal = state["current_sub_goal"]
    service_name = sub_goal["name"]
    artifacts = state.get("dev_artifacts") or {}
    files: list[str] = artifacts.get("files", [])

    log_dir = _log_dir(state)

    checks = []

    # ① path prefix (항상 먼저 — 위반 시 이후 체크도 계속 실행)
    checks.append(static.check_path_prefix(files, log_dir=log_dir))

    # ② helm chart 체크
    if _has_helm(files, service_name):
        chart_path = _chart_path(service_name)
        release_name = f"{service_name}-dev-v1"
        vf = _values_files(chart_path)

        checks.append(static.check_yamllint(chart_path, log_dir=log_dir))
        checks.append(static.check_helm_lint(chart_path, vf, log_dir=log_dir))
        checks.append(static.check_helm_template_kubeconform(
            chart_path, release_name, NAMESPACE, vf, log_dir=log_dir))
        checks.append(static.check_trivy_config(chart_path, log_dir=log_dir))
        checks.append(static.check_gitleaks(chart_path, log_dir=log_dir))
        checks.append(static.check_helm_dry_run_server(
            chart_path, release_name, NAMESPACE, vf, log_dir=log_dir))

    # ③ raw manifest 체크
    if _has_manifests(files, service_name):
        manifest_dir = _manifest_dir(service_name)

        checks.append(static.check_yamllint(manifest_dir, log_dir=log_dir))
        checks.append(static.check_kubeconform(manifest_dir, log_dir=log_dir))
        checks.append(static.check_trivy_config(manifest_dir, log_dir=log_dir))
        checks.append(static.check_gitleaks(manifest_dir, log_dir=log_dir))
        checks.append(static.check_kubectl_dry_run_server(
            manifest_dir, NAMESPACE, log_dir=log_dir))

    # ④ 어느 쪽도 없으면 fail
    if not _has_helm(files, service_name) and not _has_manifests(files, service_name):
        checks.append({
            "name": "artifact_detection",
            "status": "fail",
            "detail": f"No helm chart or manifests found for service '{service_name}' in dev_artifacts",
            "log_path": None,
        })

    passed = all(c["status"] in ("pass", "skip") for c in checks)

    return {
        "current_sub_goal": {**sub_goal, "stage": "static_verify"},
        "static_verification": {"checks": checks},
        "verification": {
            "passed": passed,
            "stage": "static",
            "checks": checks,
            "log_dir": str(Path(log_dir).parent) + "/",  # .../attempt_N/
        },
    }
