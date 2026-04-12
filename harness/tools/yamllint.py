from pathlib import Path

from harness.tools.shell import run


def lint(path: str, config: str = "relaxed") -> dict:
    """
    yamllint 실행. config: relaxed | default | 파일경로.
    디렉토리인 경우 helm templates/ 제외 (Go template 구문은 유효한 YAML이 아님).
    """
    p = Path(path)
    if p.is_dir():
        files = sorted(
            str(f) for f in p.rglob("*.yaml")
            if "templates" not in f.relative_to(p).parts
        )
        if not files:
            return {"stdout": "", "stderr": "", "exit_code": 0, "command": "yamllint (no files)"}
        return run(["yamllint", "-d", config, "-f", "parsable"] + files)
    return run(["yamllint", "-d", config, "-f", "parsable", path])
