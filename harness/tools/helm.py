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
    timeout: str = "120s",
) -> dict:
    cmd = [
        "helm", "upgrade", "--install", release_name, chart_path,
        "-n", namespace,
        "--timeout", timeout,
        "--wait",
    ]
    for vf in (values_files or []):
        cmd += ["-f", vf]
    return run(cmd, timeout=int(timeout.rstrip("s")) + 30)


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
