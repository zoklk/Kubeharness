"""
정적 검증 함수 모음. LLM 없음, 순수 Python + CLI 래퍼.

각 함수 반환:
    {"name": str, "status": "pass"|"fail"|"skip", "detail": str, "log_path": str|None}

- log_dir 지정 시 결과를 파일로 저장하고 log_path 반환
- 각 체크는 독립적으로 실행 (하나 fail이어도 다음 계속)
"""

from pathlib import Path
from typing import Optional

from harness.tools import helm, kubectl, yamllint, kubeconform, trivy, gitleaks, shell


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _result(name: str, status: str, detail: str, log_dir: Optional[str], raw: str = "") -> dict:
    log_path = None
    if log_dir:
        p = Path(log_dir) / f"{name}.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(raw or detail, encoding="utf-8")
        log_path = str(p)
    return {"name": name, "status": status, "detail": detail, "log_path": log_path}


def _from_run(name: str, r: dict, log_dir: Optional[str]) -> dict:
    status = "pass" if r["exit_code"] == 0 else "fail"
    detail = (r["stdout"] + r["stderr"]).strip() or "OK"
    return _result(name, status, detail, log_dir, r["stdout"] + r["stderr"])


# ── 체크 함수 ─────────────────────────────────────────────────────────────────

def check_yamllint(path: str, log_dir: Optional[str] = None) -> dict:
    r = yamllint.lint(path)
    return _from_run("yamllint", r, log_dir)


def check_kubeconform(path: str, log_dir: Optional[str] = None) -> dict:
    r = kubeconform.validate(path)
    return _from_run("kubeconform", r, log_dir)


def check_helm_lint(chart_path: str, values_files: Optional[list[str]] = None,
                    log_dir: Optional[str] = None) -> dict:
    r = helm.lint(chart_path, values_files)
    return _from_run("helm_lint", r, log_dir)


def check_helm_template_kubeconform(
    chart_path: str,
    release_name: str,
    namespace: str,
    values_files: Optional[list[str]] = None,
    log_dir: Optional[str] = None,
) -> dict:
    helm_cmd = ["helm", "template", release_name, chart_path, "-n", namespace]
    for vf in (values_files or []):
        helm_cmd += ["-f", vf]
    r = kubeconform.validate_stdin(helm_cmd)
    return _from_run("helm_template_kubeconform", r, log_dir)


def check_trivy_config(path: str, log_dir: Optional[str] = None) -> dict:
    r = trivy.config(path)
    return _from_run("trivy_config", r, log_dir)


def check_gitleaks(path: str, log_dir: Optional[str] = None) -> dict:
    r = gitleaks.detect(path)
    return _from_run("gitleaks", r, log_dir)


def check_helm_dry_run_server(
    chart_path: str,
    release_name: str,
    namespace: str,
    values_files: Optional[list[str]] = None,
    log_dir: Optional[str] = None,
) -> dict:
    r = helm.dry_run_server(release_name, chart_path, namespace, values_files)
    # immutable 충돌은 stderr에 포함되므로 stderr 우선
    detail = (r["stderr"] or r["stdout"]).strip() or "OK"
    status = "pass" if r["exit_code"] == 0 else "fail"
    return _result("helm_dry_run_server", status, detail, log_dir, r["stdout"] + r["stderr"])


def check_kubectl_dry_run_server(
    manifest_path: str,
    namespace: str,
    log_dir: Optional[str] = None,
) -> dict:
    r = kubectl.dry_run_server(manifest_path, namespace)
    detail = (r["stderr"] or r["stdout"]).strip() or "OK"
    status = "pass" if r["exit_code"] == 0 else "fail"
    return _result("kubectl_dry_run_server", status, detail, log_dir, r["stdout"] + r["stderr"])


def check_dockerfile(docker_dir: str, log_dir: Optional[str] = None) -> dict:
    """
    Dockerfile 정적 검사.
    - hadolint 설치 시: lint 실행
    - 미설치 시: Dockerfile 존재 여부만 확인(skip)
    """
    dockerfile = Path(docker_dir) / "Dockerfile"
    if not dockerfile.exists():
        detail = f"Dockerfile not found at {dockerfile}"
        return _result("dockerfile", "fail", detail, log_dir, detail)

    r = shell.run(["hadolint", str(dockerfile)])
    if r["exit_code"] == 127 or "not found" in r["stderr"].lower():
        return _result("dockerfile", "skip", "hadolint not installed", log_dir)
    return _from_run("dockerfile", r, log_dir)


def check_path_prefix(
    files: list[str],
    allowed_prefix: str = "edge-server/",
    log_dir: Optional[str] = None,
) -> dict:
    """Developer가 생성한 파일이 모두 allowed_prefix로 시작하는지 검증."""
    violations = [f for f in files if not f.startswith(allowed_prefix)]
    if violations:
        detail = f"Path prefix violation: {violations}"
        return _result("path_prefix", "fail", detail, log_dir, detail)
    return _result("path_prefix", "pass", f"All files under {allowed_prefix}", log_dir)
