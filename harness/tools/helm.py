from harness.tools.shell import run


def lint(chart_path: str, values_files: list[str] | None = None) -> dict:
    cmd = ["helm", "lint", chart_path]
    for vf in (values_files or []):
        cmd += ["-f", vf]
    return run(cmd)


def template(
    chart_path: str,
    release_name: str,
    namespace: str,
    values_files: list[str] | None = None,
) -> dict:
    cmd = ["helm", "template", release_name, chart_path, "-n", namespace]
    for vf in (values_files or []):
        cmd += ["-f", vf]
    return run(cmd)


def upgrade_install(
    release_name: str,
    chart_path: str,
    namespace: str,
    values_files: list[str] | None = None,
) -> dict:
    """
    매니페스트를 API 서버에 적용만 함. --wait 없음.
    파드 Ready 대기는 runtime_gates의 kubectl wait 단계에서 수행.
    """
    cmd = [
        "helm", "upgrade", "--install", release_name, chart_path,
        "-n", namespace,
    ]
    for vf in (values_files or []):
        cmd += ["-f", vf]
    return run(cmd)


def uninstall(release_name: str, namespace: str) -> dict:
    cmd = ["helm", "uninstall", release_name, "-n", namespace]
    return run(cmd)


def dry_run_server(
    release_name: str,
    chart_path: str,
    namespace: str,
    values_files: list[str] | None = None,
) -> dict:
    cmd = [
        "helm", "upgrade", "--install", release_name, chart_path,
        "-n", namespace,
        "--dry-run=server",
    ]
    for vf in (values_files or []):
        cmd += ["-f", vf]
    return run(cmd)
