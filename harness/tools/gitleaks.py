from harness.tools.shell import run


def detect(path: str) -> dict:
    """gitleaks 시크릿 스캔. --no-git으로 git 히스토리 무시, 파일 직접 스캔."""
    return run(["gitleaks", "detect", "--source", path, "--no-git", "--exit-code", "1"])
