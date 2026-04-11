"""
harness/nodes/static_verifier.py 단위 테스트
static.check_* 함수를 mock해서 노드 로직만 검증.
"""

from unittest.mock import patch, call
import pytest

from harness.nodes.static_verifier import static_verifier_node, PROJECT_ROOT

SERVICE = "myapp"

# ── 공통 픽스처 ────────────────────────────────────────────────────────────────

def _pass(name):
    return {"name": name, "status": "pass", "detail": "OK", "log_path": None}

def _fail(name, detail="error"):
    return {"name": name, "status": "fail", "detail": detail, "log_path": None}

def _state(files: list[str], service: str = SERVICE) -> dict:
    return {
        "current_phase": "test",
        "current_sub_goal": {"name": service, "phase": "test", "stage": "dev"},
        "dev_artifacts": {"files": files, "notes": ""},
        "history": [],
        "error_count": 0,
    }

HELM_FILES = [
    f"edge-server/helm/{SERVICE}/Chart.yaml",
    f"edge-server/helm/{SERVICE}/values.yaml",
    f"edge-server/helm/{SERVICE}/templates/deployment.yaml",
]

MANIFEST_FILES = [
    f"edge-server/manifests/{SERVICE}/deployment.yaml",
    f"edge-server/manifests/{SERVICE}/service.yaml",
]


# ── helm chart 경로 ───────────────────────────────────────────────────────────

HELM_CHECKS = [
    "harness.verifiers.static.check_path_prefix",
    "harness.verifiers.static.check_yamllint",
    "harness.verifiers.static.check_helm_lint",
    "harness.verifiers.static.check_helm_template_kubeconform",
    "harness.verifiers.static.check_trivy_config",
    "harness.verifiers.static.check_gitleaks",
    "harness.verifiers.static.check_helm_dry_run_server",
]

def test_helm_all_pass(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    returns = [_pass(n.split(".")[-1]) for n in HELM_CHECKS]

    with patch.multiple("harness.verifiers.static",
                        check_path_prefix=lambda *a, **kw: returns[0],
                        check_yamllint=lambda *a, **kw: returns[1],
                        check_helm_lint=lambda *a, **kw: returns[2],
                        check_helm_template_kubeconform=lambda *a, **kw: returns[3],
                        check_trivy_config=lambda *a, **kw: returns[4],
                        check_gitleaks=lambda *a, **kw: returns[5],
                        check_helm_dry_run_server=lambda *a, **kw: returns[6]):
        result = static_verifier_node(_state(HELM_FILES))

    assert result["verification"]["passed"] is True
    assert result["verification"]["stage"] == "static"
    assert len(result["verification"]["checks"]) == 7
    assert result["current_sub_goal"]["stage"] == "static_verify"


def test_helm_one_fail_passed_false(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with patch.multiple("harness.verifiers.static",
                        check_path_prefix=lambda *a, **kw: _pass("path_prefix"),
                        check_yamllint=lambda *a, **kw: _fail("yamllint", "syntax error"),
                        check_helm_lint=lambda *a, **kw: _pass("helm_lint"),
                        check_helm_template_kubeconform=lambda *a, **kw: _pass("htk"),
                        check_trivy_config=lambda *a, **kw: _pass("trivy_config"),
                        check_gitleaks=lambda *a, **kw: _pass("gitleaks"),
                        check_helm_dry_run_server=lambda *a, **kw: _pass("helm_dry_run_server")):
        result = static_verifier_node(_state(HELM_FILES))

    assert result["verification"]["passed"] is False
    # 나머지 체크는 계속 실행됨
    assert len(result["verification"]["checks"]) == 7


def test_helm_chart_path_passed_to_checks(tmp_path, monkeypatch):
    """check 함수들이 올바른 chart_path / release_name / namespace로 호출되는지."""
    monkeypatch.chdir(tmp_path)

    with (
        patch("harness.verifiers.static.check_path_prefix", return_value=_pass("pp")) as m_pp,
        patch("harness.verifiers.static.check_yamllint", return_value=_pass("yl")) as m_yl,
        patch("harness.verifiers.static.check_helm_lint", return_value=_pass("hl")) as m_hl,
        patch("harness.verifiers.static.check_helm_template_kubeconform", return_value=_pass("htk")) as m_htk,
        patch("harness.verifiers.static.check_trivy_config", return_value=_pass("tc")) as m_tc,
        patch("harness.verifiers.static.check_gitleaks", return_value=_pass("gl")) as m_gl,
        patch("harness.verifiers.static.check_helm_dry_run_server", return_value=_pass("hd")) as m_hd,
    ):
        static_verifier_node(_state(HELM_FILES))

    expected_chart = str(PROJECT_ROOT / f"edge-server/helm/{SERVICE}")
    expected_release = f"{SERVICE}-dev-v1"

    assert m_yl.call_args[0][0] == expected_chart
    assert m_hl.call_args[0][0] == expected_chart
    assert m_htk.call_args[0][0] == expected_chart
    assert m_htk.call_args[0][1] == expected_release
    assert m_htk.call_args[0][2] == "gikview"
    assert m_hd.call_args[0][0] == expected_chart
    assert m_hd.call_args[0][1] == expected_release
    assert m_hd.call_args[0][2] == "gikview"


# ── manifest 경로 ─────────────────────────────────────────────────────────────

MANIFEST_CHECKS = [
    "check_path_prefix",
    "check_yamllint",
    "check_kubeconform",
    "check_trivy_config",
    "check_gitleaks",
    "check_kubectl_dry_run_server",
]

def test_manifest_all_pass(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with patch.multiple("harness.verifiers.static",
                        check_path_prefix=lambda *a, **kw: _pass("path_prefix"),
                        check_yamllint=lambda *a, **kw: _pass("yamllint"),
                        check_kubeconform=lambda *a, **kw: _pass("kubeconform"),
                        check_trivy_config=lambda *a, **kw: _pass("trivy_config"),
                        check_gitleaks=lambda *a, **kw: _pass("gitleaks"),
                        check_kubectl_dry_run_server=lambda *a, **kw: _pass("kubectl_dry_run_server")):
        result = static_verifier_node(_state(MANIFEST_FILES))

    assert result["verification"]["passed"] is True
    assert len(result["verification"]["checks"]) == 6


def test_manifest_dir_passed_to_checks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with (
        patch("harness.verifiers.static.check_path_prefix", return_value=_pass("pp")),
        patch("harness.verifiers.static.check_yamllint", return_value=_pass("yl")) as m_yl,
        patch("harness.verifiers.static.check_kubeconform", return_value=_pass("kc")) as m_kc,
        patch("harness.verifiers.static.check_trivy_config", return_value=_pass("tc")) as m_tc,
        patch("harness.verifiers.static.check_gitleaks", return_value=_pass("gl")) as m_gl,
        patch("harness.verifiers.static.check_kubectl_dry_run_server", return_value=_pass("kd")) as m_kd,
    ):
        static_verifier_node(_state(MANIFEST_FILES))

    expected_dir = str(PROJECT_ROOT / f"edge-server/manifests/{SERVICE}")
    assert m_yl.call_args[0][0] == expected_dir
    assert m_kc.call_args[0][0] == expected_dir
    assert m_kd.call_args[0][0] == expected_dir
    assert m_kd.call_args[0][1] == "gikview"


# ── path prefix 위반 ──────────────────────────────────────────────────────────

def test_path_prefix_violation_still_runs_other_checks(tmp_path, monkeypatch):
    """path_prefix fail이어도 나머지 체크는 독립 실행."""
    monkeypatch.chdir(tmp_path)
    bad_files = [f"edge-server/helm/{SERVICE}/Chart.yaml", "harness/state.py"]

    with patch.multiple("harness.verifiers.static",
                        check_path_prefix=lambda *a, **kw: _fail("path_prefix", "violation"),
                        check_yamllint=lambda *a, **kw: _pass("yamllint"),
                        check_helm_lint=lambda *a, **kw: _pass("helm_lint"),
                        check_helm_template_kubeconform=lambda *a, **kw: _pass("htk"),
                        check_trivy_config=lambda *a, **kw: _pass("trivy_config"),
                        check_gitleaks=lambda *a, **kw: _pass("gitleaks"),
                        check_helm_dry_run_server=lambda *a, **kw: _pass("hd")):
        result = static_verifier_node(_state(bad_files))

    assert result["verification"]["passed"] is False
    assert len(result["verification"]["checks"]) == 7  # 모두 실행됨


# ── dev_artifacts 없음 ────────────────────────────────────────────────────────

def test_no_artifacts_fail():
    state = _state([])
    state["dev_artifacts"] = None

    result = static_verifier_node(state)

    assert result["verification"]["passed"] is False
    names = [c["name"] for c in result["verification"]["checks"]]
    assert "artifact_detection" in names


def test_empty_files_fail():
    result = static_verifier_node(_state([]))

    assert result["verification"]["passed"] is False
    names = [c["name"] for c in result["verification"]["checks"]]
    assert "artifact_detection" in names


# ── state 필드 구조 ───────────────────────────────────────────────────────────

def test_state_fields_set(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with patch.multiple("harness.verifiers.static",
                        check_path_prefix=lambda *a, **kw: _pass("path_prefix"),
                        check_yamllint=lambda *a, **kw: _pass("yamllint"),
                        check_helm_lint=lambda *a, **kw: _pass("helm_lint"),
                        check_helm_template_kubeconform=lambda *a, **kw: _pass("htk"),
                        check_trivy_config=lambda *a, **kw: _pass("trivy_config"),
                        check_gitleaks=lambda *a, **kw: _pass("gitleaks"),
                        check_helm_dry_run_server=lambda *a, **kw: _pass("hd")):
        result = static_verifier_node(_state(HELM_FILES))

    # verification 키 존재
    v = result["verification"]
    assert "passed" in v
    assert "stage" in v
    assert "checks" in v
    assert "log_dir" in v
    # logs/raw/{phase}/{sub_goal}/attempt_{error_count}/
    assert v["log_dir"] == str(PROJECT_ROOT / f"logs/raw/test/{SERVICE}/attempt_0") + "/"

    # static_verification 키 존재 및 passed 포함 확인
    sv = result["static_verification"]
    assert "checks" in sv
    assert "passed" in sv
    assert sv["passed"] is True

    # sub_goal stage 업데이트
    assert result["current_sub_goal"]["stage"] == "static_verify"
    assert result["current_sub_goal"]["name"] == SERVICE


# ── service_name 오버라이드 ────────────────────────────────────────────────────

def test_service_name_overrides_name_for_path(tmp_path, monkeypatch):
    """sub_goal.service_name이 있으면 name 대신 사용해 경로/release_name을 결정."""
    monkeypatch.chdir(tmp_path)
    # sub_goal name은 "mqtt-mtls-listener" 이지만 service_name은 "emqx"
    alt_service = "emqx"
    files = [
        f"edge-server/helm/{alt_service}/Chart.yaml",
        f"edge-server/helm/{alt_service}/values.yaml",
    ]
    state = {
        "current_phase": "messaging",
        "current_sub_goal": {
            "name": "mqtt-mtls-listener",
            "phase": "messaging",
            "stage": "dev",
            "service_name": alt_service,   # ← 오버라이드
        },
        "dev_artifacts": {"files": files, "notes": ""},
        "history": [],
        "error_count": 0,
    }

    with (
        patch("harness.verifiers.static.check_path_prefix", return_value=_pass("pp")) as m_pp,
        patch("harness.verifiers.static.check_yamllint", return_value=_pass("yl")) as m_yl,
        patch("harness.verifiers.static.check_helm_lint", return_value=_pass("hl")) as m_hl,
        patch("harness.verifiers.static.check_helm_template_kubeconform", return_value=_pass("htk")) as m_htk,
        patch("harness.verifiers.static.check_trivy_config", return_value=_pass("tc")),
        patch("harness.verifiers.static.check_gitleaks", return_value=_pass("gl")),
        patch("harness.verifiers.static.check_helm_dry_run_server", return_value=_pass("hd")) as m_hd,
    ):
        result = static_verifier_node(state)

    expected_chart = str(PROJECT_ROOT / f"edge-server/helm/{alt_service}")
    expected_release = f"{alt_service}-dev-v1"

    # service_name 기반 경로 사용 확인
    assert m_yl.call_args[0][0] == expected_chart
    assert m_htk.call_args[0][1] == expected_release
    assert result["verification"]["passed"] is True


# ── static_verification 필드 ────────────────────────────────────────────────

def test_static_verification_has_passed_field(tmp_path, monkeypatch):
    """static_verification dict에 passed 필드가 존재하고 routing에 사용 가능."""
    monkeypatch.chdir(tmp_path)

    with patch.multiple("harness.verifiers.static",
                        check_path_prefix=lambda *a, **kw: _pass("path_prefix"),
                        check_yamllint=lambda *a, **kw: _pass("yamllint"),
                        check_helm_lint=lambda *a, **kw: _pass("helm_lint"),
                        check_helm_template_kubeconform=lambda *a, **kw: _pass("htk"),
                        check_trivy_config=lambda *a, **kw: _pass("trivy_config"),
                        check_gitleaks=lambda *a, **kw: _pass("gitleaks"),
                        check_helm_dry_run_server=lambda *a, **kw: _pass("hd")):
        result = static_verifier_node(_state(HELM_FILES))

    assert result["static_verification"]["passed"] is True


def test_static_verification_passed_false_on_fail(tmp_path, monkeypatch):
    """체크 실패 시 static_verification.passed=False."""
    monkeypatch.chdir(tmp_path)

    with patch.multiple("harness.verifiers.static",
                        check_path_prefix=lambda *a, **kw: _pass("path_prefix"),
                        check_yamllint=lambda *a, **kw: _fail("yamllint", "indent error"),
                        check_helm_lint=lambda *a, **kw: _pass("helm_lint"),
                        check_helm_template_kubeconform=lambda *a, **kw: _pass("htk"),
                        check_trivy_config=lambda *a, **kw: _pass("trivy_config"),
                        check_gitleaks=lambda *a, **kw: _pass("gitleaks"),
                        check_helm_dry_run_server=lambda *a, **kw: _pass("hd")):
        result = static_verifier_node(_state(HELM_FILES))

    assert result["static_verification"]["passed"] is False
    # verification.passed와 일치
    assert result["static_verification"]["passed"] == result["verification"]["passed"]


# ── _values_files active env ─────────────────────────────────────────────────

def test_values_files_uses_active_env_dev(tmp_path):
    """active=dev이면 values-dev.yaml을 선택한다."""
    from harness.nodes.static_verifier import _values_files

    (tmp_path / "values.yaml").write_text("x: 1\n")
    (tmp_path / "values-dev.yaml").write_text("x: 2\n")
    (tmp_path / "values-prod.yaml").write_text("x: 3\n")

    with patch("harness.config.cluster_config", return_value={"_active": "dev"}):
        result = _values_files(str(tmp_path))

    assert str(tmp_path / "values.yaml") in result
    assert str(tmp_path / "values-dev.yaml") in result
    assert str(tmp_path / "values-prod.yaml") not in result


def test_values_files_uses_active_env_prod(tmp_path):
    """active=prod이면 values-prod.yaml을 선택하고 values-dev.yaml은 제외."""
    from harness.nodes.static_verifier import _values_files

    (tmp_path / "values.yaml").write_text("x: 1\n")
    (tmp_path / "values-dev.yaml").write_text("x: 2\n")
    (tmp_path / "values-prod.yaml").write_text("x: 3\n")

    with patch("harness.config.cluster_config", return_value={"_active": "prod"}):
        result = _values_files(str(tmp_path))

    assert str(tmp_path / "values.yaml") in result
    assert str(tmp_path / "values-prod.yaml") in result
    assert str(tmp_path / "values-dev.yaml") not in result


def test_values_files_missing_env_file_excluded(tmp_path):
    """active 환경의 values 파일이 없으면 목록에서 제외된다."""
    from harness.nodes.static_verifier import _values_files

    (tmp_path / "values.yaml").write_text("x: 1\n")
    # values-dev.yaml 없음

    with patch("harness.config.cluster_config", return_value={"_active": "dev"}):
        result = _values_files(str(tmp_path))

    assert result == [str(tmp_path / "values.yaml")]
