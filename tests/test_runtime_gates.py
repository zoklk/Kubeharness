"""
harness/verifiers/runtime_gates.py 단위 테스트
실제 클러스터 호출 없이 subprocess를 mock하여 검증.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.verifiers.runtime_gates import (
    run_runtime_phase1,
    _is_terminal_failure, _terminal_detail,
)

SERVICE = "myapp"


# ── 공통 mock 반환값 헬퍼 ─────────────────────────────────────────────────────

def _ok(stdout="OK", stderr=""):
    return {"stdout": stdout, "stderr": stderr, "exit_code": 0, "command": ""}

def _fail(stderr="error", stdout=""):
    return {"stdout": stdout, "stderr": stderr, "exit_code": 1, "command": ""}


# ── autouse: 기본 helm 디렉토리 생성 ─────────────────────────────────────────
#
# 대부분의 테스트가 helm path를 전제하므로 PROJECT_ROOT를 tmp_path로 교체하고
# edge-server/helm/<SERVICE>/ 디렉토리를 자동 생성한다.
# manifest-path 테스트는 별도로 manifest_dir을 생성하고 helm_dir을 제거한다.

@pytest.fixture(autouse=True)
def _default_helm_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.verifiers.runtime_gates.PROJECT_ROOT", tmp_path)
    (tmp_path / "edge-server" / "helm" / SERVICE).mkdir(parents=True, exist_ok=True)
    return tmp_path


# ── 전체 성공 경로 ─────────────────────────────────────────────────────────────

def test_all_pass_no_smoke(tmp_path):
    """smoke test 없을 때 helm_install, kubectl_wait pass + smoke skip."""
    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()) as m_helm,
        patch("harness.tools.kubectl.wait", return_value=_ok()) as m_wait,
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is True
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["helm_install"] == "pass"
    assert statuses["kubectl_wait"] == "pass"
    assert statuses["smoke_test"] == "skip"

    m_helm.assert_called_once_with(
        f"{SERVICE}-dev-v1",
        str(tmp_path / f"edge-server/helm/{SERVICE}"),
        "gikview",
        [],  # values.yaml 없으면 빈 리스트
    )
    m_wait.assert_called_once_with(
        "pods", "Ready", "gikview",
        label=f"app.kubernetes.io/name={SERVICE}",
        timeout="60s",
    )


def test_all_pass_with_smoke(tmp_path, monkeypatch):
    """smoke test 스크립트가 존재하고 통과하면 passed=True."""
    smoke_dir = tmp_path / "edge-server" / "scripts"
    smoke_dir.mkdir(parents=True)
    (smoke_dir / f"smoke-test-{SERVICE}.sh").write_text("exit 0")

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()),
        patch("harness.tools.kubectl.wait", return_value=_ok()),
        patch("harness.tools.shell.run", return_value=_ok("smoke ok")) as m_smoke,
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is True
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["smoke_test"] == "pass"
    args = m_smoke.call_args[0][0]
    assert args[0] == "bash"
    assert f"smoke-test-{SERVICE}.sh" in args[1]


# ── 배포 아티팩트 없음 ────────────────────────────────────────────────────────

def test_no_artifacts_fail(tmp_path, monkeypatch):
    """helm chart도 manifest dir도 없으면 즉시 fail."""
    import shutil
    shutil.rmtree(tmp_path / "edge-server" / "helm" / SERVICE)

    result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    assert result["checks"][0]["name"] == "deploy"
    assert result["checks"][0]["status"] == "fail"


# ── helm_install fail ─────────────────────────────────────────────────────────

def test_helm_fail_skips_rest():
    """helm install 일반 실패(immutable 아님) 시 나머지 skip, passed=False."""
    with patch("harness.tools.helm.upgrade_install", return_value=_fail("connection timed out")):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["helm_install"] == "fail"
    assert statuses["kubectl_wait"] == "skip"
    assert statuses["smoke_test"] == "skip"
    helm_check = next(c for c in result["checks"] if c["name"] == "helm_install")
    assert "connection timed out" in helm_check["detail"]


def test_helm_immutable_uninstall_then_reinstall_success():
    """immutable 에러 → uninstall 성공 → 재설치 성공 → passed=True."""
    install_responses = [
        _fail("StatefulSet.apps field is immutable"),
        _ok("deployed"),
    ]
    with (
        patch("harness.tools.helm.upgrade_install", side_effect=install_responses) as m_helm,
        patch("harness.tools.helm.uninstall", return_value=_ok()) as m_uninstall,
        patch("harness.tools.kubectl.wait", return_value=_ok()),
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is True
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["helm_uninstall_immutable"] == "pass"
    assert statuses["helm_install"] == "pass"
    assert m_helm.call_count == 2
    m_uninstall.assert_called_once()


def test_helm_immutable_uninstall_fails():
    """immutable 에러 → uninstall 실패 → passed=False, 이후 skip."""
    with (
        patch("harness.tools.helm.upgrade_install", return_value=_fail("immutable")),
        patch("harness.tools.helm.uninstall", return_value=_fail("release not found")) as m_uninstall,
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["helm_uninstall_immutable"] == "fail"
    assert statuses["helm_install"] == "skip"
    assert statuses["kubectl_wait"] == "skip"
    m_uninstall.assert_called_once()


def test_helm_immutable_reinstall_fails():
    """immutable 에러 → uninstall 성공 → 재설치 실패 → passed=False."""
    install_responses = [
        _fail("immutable field detected"),
        _fail("CrashLoopBackOff"),
    ]
    with (
        patch("harness.tools.helm.upgrade_install", side_effect=install_responses),
        patch("harness.tools.helm.uninstall", return_value=_ok()),
        patch("harness.tools.kubectl.wait", return_value=_ok()),
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["helm_uninstall_immutable"] == "pass"
    assert statuses["helm_install"] == "fail"
    assert statuses["kubectl_wait"] == "skip"


# ── kubectl_wait fail ─────────────────────────────────────────────────────────

def test_kubectl_wait_fail_non_terminal():
    """kubectl_wait 실패(non-terminal) 시 240s 추가 대기 후 fail, smoke skip."""
    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()),
        patch("harness.tools.kubectl.wait", return_value=_fail("timed out")) as m_wait,
        patch("harness.tools.kubectl.get_pods", return_value=_pods_json_pending()),
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["kubectl_wait"] == "fail"
    assert statuses["smoke_test"] == "skip"
    assert m_wait.call_count == 2  # 60s + 240s


# ── smoke_test fail ───────────────────────────────────────────────────────────

def test_smoke_fail(tmp_path, monkeypatch):
    smoke_dir = tmp_path / "edge-server" / "scripts"
    smoke_dir.mkdir(parents=True)
    (smoke_dir / f"smoke-test-{SERVICE}.sh").write_text("exit 1")

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()),
        patch("harness.tools.kubectl.wait", return_value=_ok()),
        patch("harness.tools.shell.run", return_value=_fail("connection refused")),
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    smoke_check = next(c for c in result["checks"] if c["name"] == "smoke_test")
    assert smoke_check["status"] == "fail"


# ── values_files 포함 여부 ────────────────────────────────────────────────────

def test_values_files_included_when_present(tmp_path, monkeypatch):
    """values.yaml, values-dev.yaml 모두 존재 시 둘 다 전달."""
    chart_dir = tmp_path / "edge-server" / "helm" / SERVICE
    chart_dir.mkdir(parents=True, exist_ok=True)
    (chart_dir / "values.yaml").write_text("replicas: 1")
    (chart_dir / "values-dev.yaml").write_text("replicas: 1")

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()) as m_helm,
        patch("harness.tools.kubectl.wait", return_value=_ok()),
    ):
        run_runtime_phase1(SERVICE)

    _, _, _, vf = m_helm.call_args[0]
    assert any("values.yaml" in f for f in vf)
    assert any("values-dev.yaml" in f for f in vf)


def test_values_dev_excluded_when_absent(tmp_path, monkeypatch):
    """values-dev.yaml 없으면 values.yaml만 전달."""
    chart_dir = tmp_path / "edge-server" / "helm" / SERVICE
    chart_dir.mkdir(parents=True, exist_ok=True)
    (chart_dir / "values.yaml").write_text("replicas: 1")

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()) as m_helm,
        patch("harness.tools.kubectl.wait", return_value=_ok()),
    ):
        run_runtime_phase1(SERVICE)

    _, _, _, vf = m_helm.call_args[0]
    assert len(vf) == 1
    assert "values.yaml" in vf[0]
    assert not any("values-dev" in f for f in vf)


# ── manifest-only 경로 ────────────────────────────────────────────────────────

def test_manifest_deploy_pass(tmp_path, monkeypatch):
    """manifest dir 존재, kubectl apply 성공 → passed=True, pod wait 없음."""
    import shutil
    shutil.rmtree(tmp_path / "edge-server" / "helm" / SERVICE)
    (tmp_path / "edge-server" / "manifests" / SERVICE).mkdir(parents=True)

    with (
        patch("harness.tools.kubectl.apply", return_value=_ok()) as m_apply,
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is True
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["kubectl_apply"] == "pass"
    assert "kubectl_wait" not in statuses   # pod wait 없음
    assert statuses["smoke_test"] == "skip"
    m_apply.assert_called_once_with(
        str(tmp_path / "edge-server" / "manifests" / SERVICE),
        "gikview",
    )


def test_manifest_deploy_fail(tmp_path, monkeypatch):
    """kubectl apply 실패 → kubectl_apply=fail, 이후 skip."""
    import shutil
    shutil.rmtree(tmp_path / "edge-server" / "helm" / SERVICE)
    (tmp_path / "edge-server" / "manifests" / SERVICE).mkdir(parents=True)

    with patch("harness.tools.kubectl.apply", return_value=_fail("CRD not found")):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["kubectl_apply"] == "fail"
    assert statuses["smoke_test"] == "skip"


# ── log_dir 저장 확인 ─────────────────────────────────────────────────────────

def test_log_dir_saved(tmp_path):
    log_dir = str(tmp_path / "logs")

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok("helm output")),
        patch("harness.tools.kubectl.wait", return_value=_ok()),
    ):
        result = run_runtime_phase1(SERVICE, log_dir=log_dir)

    helm_check = next(c for c in result["checks"] if c["name"] == "helm_install")
    assert helm_check["log_path"] is not None
    assert Path(helm_check["log_path"]).exists()


# ── terminal 상태 감지 헬퍼 ──────────────────────────────────────────────────

def _pods_json_pending() -> dict:
    """ContainerCreating — terminal 아님, 정상 기동 중."""
    items = [{"metadata": {"name": "pod-0"}, "status": {"containerStatuses": [
        {"name": "app", "state": {"waiting": {"reason": "ContainerCreating"}}, "ready": False}
    ]}}]
    return {"exit_code": 0, "stdout": json.dumps({"items": items}), "stderr": "", "command": ""}


def _pods_json_terminal(reason: str = "CrashLoopBackOff") -> dict:
    """terminal 상태 pod."""
    items = [{"metadata": {"name": "pod-0"}, "status": {"containerStatuses": [
        {"name": "app", "state": {"waiting": {"reason": reason, "message": "back-off 5m0s restarting"}}, "ready": False}
    ]}}]
    return {"exit_code": 0, "stdout": json.dumps({"items": items}), "stderr": "", "command": ""}


# ── _is_terminal_failure / _terminal_detail 직접 테스트 ──────────────────────

def test_is_terminal_failure_crashloop():
    assert _is_terminal_failure(_pods_json_terminal("CrashLoopBackOff")) is True

def test_is_terminal_failure_imagepull():
    assert _is_terminal_failure(_pods_json_terminal("ImagePullBackOff")) is True

def test_is_terminal_failure_pending_is_false():
    assert _is_terminal_failure(_pods_json_pending()) is False

def test_is_terminal_failure_kubectl_error():
    r = {"exit_code": 1, "stdout": "", "stderr": "connection refused", "command": ""}
    assert _is_terminal_failure(r) is False

def test_terminal_detail_includes_reason():
    detail = _terminal_detail(_pods_json_terminal("CrashLoopBackOff"))
    assert "CrashLoopBackOff" in detail
    assert "pod-0" in detail


# ── 2단계 kubectl wait 통합 테스트 ──────────────────────────────────────────

def test_kubectl_wait_terminal_early_exit():
    """60s 후 CrashLoopBackOff → 조기 종료, 240s wait 없음."""
    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()),
        patch("harness.tools.kubectl.wait", return_value=_fail("timed out")) as m_wait,
        patch("harness.tools.kubectl.get_pods", return_value=_pods_json_terminal("CrashLoopBackOff")),
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["kubectl_wait"] == "fail"
    wait_detail = next(c["detail"] for c in result["checks"] if c["name"] == "kubectl_wait")
    assert "early exit" in wait_detail
    assert "CrashLoopBackOff" in wait_detail
    m_wait.assert_called_once()   # 60s 호출 1회, 240s 없음


def test_kubectl_wait_pending_then_240s_pass():
    """60s 후 Pending(non-terminal) → 240s 추가 대기 → 통과."""
    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()),
        patch("harness.tools.kubectl.wait", side_effect=[_fail("timed out"), _ok()]) as m_wait,
        patch("harness.tools.kubectl.get_pods", return_value=_pods_json_pending()),
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is True
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["kubectl_wait"] == "pass"
    assert m_wait.call_count == 2  # 60s + 240s


def test_kubectl_wait_pending_then_240s_fail():
    """60s 후 Pending → 240s도 timeout → fail."""
    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()),
        patch("harness.tools.kubectl.wait", return_value=_fail("timed out")) as m_wait,
        patch("harness.tools.kubectl.get_pods", return_value=_pods_json_pending()),
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["kubectl_wait"] == "fail"
    assert m_wait.call_count == 2  # 60s + 240s
