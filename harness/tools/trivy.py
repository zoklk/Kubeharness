from harness.tools.shell import run


def config(path: str) -> dict:
    """trivy config 보안 스캔. exit_code=1이면 취약점 발견."""
    return run(["trivy", "config", "--exit-code", "1", path])
