from harness.tools.shell import run, pipe


def validate(path: str) -> dict:
    """파일/디렉토리를 kubeconform -strict으로 검증. CRD 등 알 수 없는 스키마는 skip."""
    return run(["kubeconform", "-strict", "-summary", "-ignore-missing-schemas", path])


def validate_stdin(helm_cmd: list[str]) -> dict:
    """helm template 출력을 stdin으로 받아 검증. helm_cmd는 helm template 명령 리스트."""
    return pipe(helm_cmd, ["kubeconform", "-strict", "-summary", "-ignore-missing-schemas", "-"])
