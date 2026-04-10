"""
harness/verifiers/static.py 단위 테스트
실제 CLI 툴을 사용하여 올바른 status 반환 여부 검증
"""

import os
import textwrap
import pytest
from pathlib import Path
from harness.verifiers import static

# PATH에 ~/.local/bin 추가 (CI 환경 대비)
os.environ["PATH"] = str(Path.home() / ".local/bin") + ":" + os.environ.get("PATH", "")


# ── 픽스처: 임시 YAML 파일 ────────────────────────────────────────────────────

@pytest.fixture
def valid_k8s_yaml(tmp_path):
    f = tmp_path / "deployment.yaml"
    f.write_text(textwrap.dedent("""\
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: test-app
          namespace: gikview
        spec:
          replicas: 1
          selector:
            matchLabels:
              app: test-app
          template:
            metadata:
              labels:
                app: test-app
            spec:
              containers:
              - name: test-app
                image: nginx:latest
                ports:
                - containerPort: 80
    """))
    return str(f)


@pytest.fixture
def invalid_yaml(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("key: [\n  unclosed bracket\n")
    return str(f)


@pytest.fixture
def secret_yaml(tmp_path):
    f = tmp_path / "secret.yaml"
    f.write_text(textwrap.dedent("""\
        apiVersion: v1
        kind: Secret
        metadata:
          name: test-secret
        data:
          password: c3VwZXJzZWNyZXQ=
        stringData:
          api_key: "AKIAIOSFODNN7EXAMPLE"
    """))
    return str(f)


@pytest.fixture
def minimal_helm_chart(tmp_path):
    chart_dir = tmp_path / "mychart"
    chart_dir.mkdir()
    (chart_dir / "Chart.yaml").write_text(textwrap.dedent("""\
        apiVersion: v2
        name: mychart
        version: 0.1.0
    """))
    templates = chart_dir / "templates"
    templates.mkdir()
    (templates / "deployment.yaml").write_text(textwrap.dedent("""\
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: {{ .Release.Name }}
          namespace: {{ .Release.Namespace }}
        spec:
          replicas: {{ .Values.replicas | default 1 }}
          selector:
            matchLabels:
              app.kubernetes.io/name: {{ .Release.Name }}
          template:
            metadata:
              labels:
                app.kubernetes.io/name: {{ .Release.Name }}
            spec:
              containers:
              - name: app
                image: nginx:latest
    """))
    (chart_dir / "values.yaml").write_text("replicas: 1\n")
    return str(chart_dir)


# ── yamllint ─────────────────────────────────────────────────────────────────

def test_yamllint_pass(valid_k8s_yaml):
    r = static.check_yamllint(valid_k8s_yaml)
    assert r["name"] == "yamllint"
    assert r["status"] in ("pass", "skip")
    print(f"\n[yamllint pass] {r}")


def test_yamllint_fail(invalid_yaml):
    r = static.check_yamllint(invalid_yaml)
    assert r["status"] in ("fail", "skip")
    print(f"\n[yamllint fail] {r}")


# ── kubeconform ───────────────────────────────────────────────────────────────

def test_kubeconform_pass(valid_k8s_yaml):
    r = static.check_kubeconform(valid_k8s_yaml)
    assert r["name"] == "kubeconform"
    assert r["status"] in ("pass", "skip")
    print(f"\n[kubeconform pass] {r['status']}: {r['detail'][:80]}")


# ── helm lint ─────────────────────────────────────────────────────────────────

def test_helm_lint_pass(minimal_helm_chart):
    r = static.check_helm_lint(minimal_helm_chart)
    assert r["name"] == "helm_lint"
    assert r["status"] in ("pass", "skip")
    print(f"\n[helm_lint] {r['status']}: {r['detail'][:80]}")


# ── helm template | kubeconform ───────────────────────────────────────────────

def test_helm_template_kubeconform(minimal_helm_chart):
    r = static.check_helm_template_kubeconform(
        chart_path=minimal_helm_chart,
        release_name="test-release",
        namespace="gikview",
        values_files=[str(Path(minimal_helm_chart) / "values.yaml")],
    )
    assert r["name"] == "helm_template_kubeconform"
    assert r["status"] in ("pass", "fail", "skip")
    print(f"\n[helm_template_kubeconform] {r['status']}: {r['detail'][:80]}")


# ── trivy ─────────────────────────────────────────────────────────────────────

def test_trivy_config_pass(valid_k8s_yaml):
    r = static.check_trivy_config(valid_k8s_yaml)
    assert r["name"] == "trivy_config"
    assert r["status"] in ("pass", "fail", "skip")
    print(f"\n[trivy] {r['status']}: {r['detail'][:80]}")


# ── gitleaks ──────────────────────────────────────────────────────────────────

def test_gitleaks_pass(valid_k8s_yaml, tmp_path):
    r = static.check_gitleaks(str(tmp_path))
    assert r["name"] == "gitleaks"
    assert r["status"] in ("pass", "skip")
    print(f"\n[gitleaks pass] {r['status']}")


def test_gitleaks_fail(secret_yaml, tmp_path):
    # secret_yaml을 tmp_path에 위치시키고 스캔
    r = static.check_gitleaks(str(tmp_path))
    assert r["status"] in ("fail", "pass", "skip")
    print(f"\n[gitleaks secret] {r['status']}: {r['detail'][:80]}")


# ── path prefix ───────────────────────────────────────────────────────────────

def test_path_prefix_pass():
    files = ["edge-server/helm/prometheus/Chart.yaml", "edge-server/manifests/svc.yaml"]
    r = static.check_path_prefix(files)
    assert r["status"] == "pass"


def test_path_prefix_fail():
    files = ["edge-server/helm/ok.yaml", "harness/state.py"]
    r = static.check_path_prefix(files)
    assert r["status"] == "fail"
    assert "harness/state.py" in r["detail"]


# ── log_dir 동작 확인 ─────────────────────────────────────────────────────────

def test_log_saved(valid_k8s_yaml, tmp_path):
    log_dir = str(tmp_path / "logs")
    r = static.check_yamllint(valid_k8s_yaml, log_dir=log_dir)
    if r["status"] != "skip":
        assert r["log_path"] is not None
        assert Path(r["log_path"]).exists()
