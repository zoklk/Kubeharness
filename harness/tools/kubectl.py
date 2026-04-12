from harness.tools.shell import run


def apply(manifest_path: str, namespace: str, dry_run: bool = False) -> dict:
    cmd = ["kubectl", "apply", "-f", manifest_path, "-n", namespace]
    if dry_run:
        cmd += ["--dry-run=client"]
    return run(cmd)


def dry_run_server(manifest_path: str, namespace: str) -> dict:
    cmd = ["kubectl", "apply", "-f", manifest_path, "-n", namespace, "--dry-run=server"]
    return run(cmd)


def get(resource: str, namespace: str, output: str = "wide", label: str = "") -> dict:
    cmd = ["kubectl", "get", resource, "-n", namespace, "-o", output]
    if label:
        cmd += ["-l", label]
    return run(cmd)


def wait(
    resource: str,
    condition: str,
    namespace: str,
    label: str = "",
    timeout: str = "120s",
) -> dict:
    cmd = ["kubectl", "wait", resource, f"--for=condition={condition}", "-n", namespace, f"--timeout={timeout}"]
    if label:
        cmd += ["-l", label]
    return run(cmd, timeout=int(timeout.rstrip("s")) + 10)


def get_pods(namespace: str, label: str = "") -> dict:
    cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]
    if label:
        cmd += ["-l", label]
    return run(cmd)


def get_events(namespace: str, field_selector: str = "") -> dict:
    cmd = ["kubectl", "get", "events", "-n", namespace, "-o", "json"]
    if field_selector:
        cmd += [f"--field-selector={field_selector}"]
    return run(cmd)


def get_pod_logs(pod_name: str, namespace: str, container: str = "", tail: int = 100) -> dict:
    cmd = ["kubectl", "logs", pod_name, "-n", namespace, f"--tail={tail}"]
    if container:
        cmd += ["-c", container]
    return run(cmd)


def get_endpoints(name: str, namespace: str) -> dict:
    cmd = ["kubectl", "get", "endpoints", name, "-n", namespace, "-o", "json"]
    return run(cmd)


def describe(resource: str, name: str, namespace: str) -> dict:
    cmd = ["kubectl", "describe", resource, name, "-n", namespace]
    return run(cmd)


def delete_pods(namespace: str, label: str) -> dict:
    """label selector에 매칭되는 pod를 강제 삭제. --ignore-not-found로 없어도 OK."""
    cmd = ["kubectl", "delete", "pods", "-n", namespace, "-l", label, "--ignore-not-found"]
    return run(cmd)
