from harness.tools.shell import run


def lint(path: str, config: str = "relaxed") -> dict:
    """yamllint 실행. config: relaxed | default | 파일경로."""
    return run(["yamllint", "-d", config, "-f", "parsable", path])
