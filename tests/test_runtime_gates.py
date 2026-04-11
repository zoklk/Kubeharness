"""
harness/verifiers/runtime_gates.py 단위 테스트
실제 클러스터 호출 없이 subprocess를 mock하여 검증.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from harness.verifiers.runtime_gates import run_runtime_phase1, _parse_warning_events, PROJECT_ROOT

SERVICE = "myapp"

# ── 공통 mock 반환값 헬퍼 ─────────────────────────────────────────────────────

def _ok(stdout="OK", stderr=""):
    return {"stdout": stdout, "stderr": stderr, "exit_code": 0, "command": ""}

def _fail(stderr="error", stdout=""):
    return {"stdout": stdout, "stderr": stderr, "exit_code": 1, "command": ""}

def _events_json(items: list) -> dict:
    return {"stdout": json.dumps({"items": items}), "stderr": "", "exit_code": 0, "command": ""}

def _warning_event(msg="crash", reason="OOMKilled", obj="myapp-xxx",
                   minutes_ago: float = 1.0) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "lastTimestamp": ts,
        "reason": reason,
        "message": msg,
        "involvedObject": {"name": obj},
    }


# ── 전체 성공 경로 ─────────────────────────────────────────────────────────────

def test_all_pass_no_smoke():
    """smoke test 없을 때 4개 체크 중 3개 pass + smoke skip."""
    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()) as m_helm,
        patch("harness.tools.kubectl.wait", return_value=_ok()) as m_wait,
        patch("harness.tools.kubectl.get_events", return_value=_events_json([])) as m_ev,
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is True
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["helm_install"] == "pass"
    assert statuses["kubectl_wait"] == "pass"
    assert statuses["kubectl_events"] == "pass"
    assert statuses["smoke_test"] == "skip"

    # 명령 인자 검증
    m_helm.assert_called_once_with(
        f"{SERVICE}-dev-v1",
        str(PROJECT_ROOT / f"edge-server/helm/{SERVICE}"),
        "gikview",
        [],  # 실제 프로젝트에 edge-server/helm/myapp/values.yaml 없음
    )
    m_wait.assert_called_once_with(
        "pods", "Ready", "gikview",
        label=f"app.kubernetes.io/name={SERVICE}",
        timeout="120s",
    )
    m_ev.assert_called_once_with(
        "gikview",
        field_selector="type=Warning,involvedObject.kind=Pod",
    )


def test_all_pass_with_smoke(tmp_path, monkeypatch):
    """smoke test 스크립트가 존재하고 통과하면 passed=True."""
    monkeypatch.setattr("harness.verifiers.runtime_gates.PROJECT_ROOT", tmp_path)
    smoke_dir = tmp_path / "edge-server" / "scripts"
    smoke_dir.mkdir(parents=True)
    (smoke_dir / f"smoke-test-{SERVICE}.sh").write_text("exit 0")

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()),
        patch("harness.tools.kubectl.wait", return_value=_ok()),
        patch("harness.tools.kubectl.get_events", return_value=_events_json([])),
        patch("harness.tools.shell.run", return_value=_ok("smoke ok")) as m_smoke,
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is True
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["smoke_test"] == "pass"
    args = m_smoke.call_args[0][0]
    assert args[0] == "bash"
    assert f"smoke-test-{SERVICE}.sh" in args[1]


# ── helm_install fail ─────────────────────────────────────────────────────────

def test_helm_fail_skips_rest():
    """helm install 실패 시 나머지 3개 skip, passed=False."""
    with patch("harness.tools.helm.upgrade_install", return_value=_fail("immutable field")):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["helm_install"] == "fail"
    assert statuses["kubectl_wait"] == "skip"
    assert statuses["kubectl_events"] == "skip"
    assert statuses["smoke_test"] == "skip"
    helm_check = next(c for c in result["checks"] if c["name"] == "helm_install")
    assert "immutable field" in helm_check["detail"]


# ── kubectl_wait fail ─────────────────────────────────────────────────────────

def test_kubectl_wait_fail():
    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()),
        patch("harness.tools.kubectl.wait", return_value=_fail("timed out")),
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["kubectl_wait"] == "fail"
    assert statuses["kubectl_events"] == "skip"
    assert statuses["smoke_test"] == "skip"


# ── kubectl_events: warning 있음 ──────────────────────────────────────────────

def test_events_warning_recent_fail():
    """최근 1분 내 warning → fail."""
    ev = _warning_event(minutes_ago=1.0)

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()),
        patch("harness.tools.kubectl.wait", return_value=_ok()),
        patch("harness.tools.kubectl.get_events", return_value=_events_json([ev])),
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    ev_check = next(c for c in result["checks"] if c["name"] == "kubectl_events")
    assert ev_check["status"] == "fail"
    assert "OOMKilled" in ev_check["detail"]
    assert result["checks"][-1]["name"] == "smoke_test"
    assert result["checks"][-1]["status"] == "skip"


def test_events_warning_old_pass():
    """10분 전 warning은 무시 → pass."""
    ev = _warning_event(minutes_ago=10.0)

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()),
        patch("harness.tools.kubectl.wait", return_value=_ok()),
        patch("harness.tools.kubectl.get_events", return_value=_events_json([ev])),
    ):
        result = run_runtime_phase1(SERVICE)

    ev_check = next(c for c in result["checks"] if c["name"] == "kubectl_events")
    assert ev_check["status"] == "pass"


# ── smoke_test fail ───────────────────────────────────────────────────────────

def test_smoke_fail(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.verifiers.runtime_gates.PROJECT_ROOT", tmp_path)
    smoke_dir = tmp_path / "edge-server" / "scripts"
    smoke_dir.mkdir(parents=True)
    (smoke_dir / f"smoke-test-{SERVICE}.sh").write_text("exit 1")

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()),
        patch("harness.tools.kubectl.wait", return_value=_ok()),
        patch("harness.tools.kubectl.get_events", return_value=_events_json([])),
        patch("harness.tools.shell.run", return_value=_fail("connection refused")),
    ):
        result = run_runtime_phase1(SERVICE)

    assert result["passed"] is False
    smoke_check = next(c for c in result["checks"] if c["name"] == "smoke_test")
    assert smoke_check["status"] == "fail"


# ── values_files 포함 여부 ────────────────────────────────────────────────────

def test_values_files_included_when_present(tmp_path, monkeypatch):
    """values.yaml, values-dev.yaml 모두 존재 시 둘 다 전달."""
    monkeypatch.setattr("harness.verifiers.runtime_gates.PROJECT_ROOT", tmp_path)
    chart_dir = tmp_path / "edge-server" / "helm" / SERVICE
    chart_dir.mkdir(parents=True)
    (chart_dir / "values.yaml").write_text("replicas: 1")
    (chart_dir / "values-dev.yaml").write_text("replicas: 1")

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()) as m_helm,
        patch("harness.tools.kubectl.wait", return_value=_ok()),
        patch("harness.tools.kubectl.get_events", return_value=_events_json([])),
    ):
        run_runtime_phase1(SERVICE)

    _, _, _, vf = m_helm.call_args[0]
    assert any("values.yaml" in f for f in vf)
    assert any("values-dev.yaml" in f for f in vf)


def test_values_dev_excluded_when_absent(tmp_path, monkeypatch):
    """values-dev.yaml 없으면 values.yaml만 전달."""
    monkeypatch.setattr("harness.verifiers.runtime_gates.PROJECT_ROOT", tmp_path)
    chart_dir = tmp_path / "edge-server" / "helm" / SERVICE
    chart_dir.mkdir(parents=True)
    (chart_dir / "values.yaml").write_text("replicas: 1")

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok()) as m_helm,
        patch("harness.tools.kubectl.wait", return_value=_ok()),
        patch("harness.tools.kubectl.get_events", return_value=_events_json([])),
    ):
        run_runtime_phase1(SERVICE)

    _, _, _, vf = m_helm.call_args[0]
    assert len(vf) == 1
    assert "values.yaml" in vf[0]
    assert not any("values-dev" in f for f in vf)


# ── log_dir 저장 확인 ─────────────────────────────────────────────────────────

def test_log_dir_saved(tmp_path):
    log_dir = str(tmp_path / "logs")

    with (
        patch("harness.tools.helm.upgrade_install", return_value=_ok("helm output")),
        patch("harness.tools.kubectl.wait", return_value=_ok()),
        patch("harness.tools.kubectl.get_events", return_value=_events_json([])),
    ):
        result = run_runtime_phase1(SERVICE, log_dir=log_dir)

    helm_check = next(c for c in result["checks"] if c["name"] == "helm_install")
    assert helm_check["log_path"] is not None
    from pathlib import Path
    assert Path(helm_check["log_path"]).exists()


# ── _parse_warning_events 직접 테스트 ─────────────────────────────────────────

def test_parse_events_kubectl_error():
    r = {"stdout": "", "stderr": "connection refused", "exit_code": 1, "command": ""}
    check = _parse_warning_events(r, log_dir=None)
    assert check["status"] == "fail"
    assert "connection refused" in check["detail"]


def test_parse_events_invalid_json():
    r = {"stdout": "not json", "stderr": "", "exit_code": 0, "command": ""}
    check = _parse_warning_events(r, log_dir=None)
    assert check["status"] == "fail"
    assert "parse error" in check["detail"]
