import os
import subprocess
from pathlib import Path
from typing import Optional

from harness.config import kubeconfig_path


def _env() -> dict:
    """~/.local/bin을 PATH 앞에 추가하고, cluster.yaml의 kubeconfig를 KUBECONFIG로 주입."""
    env = os.environ.copy()
    local_bin = str(Path.home() / ".local" / "bin")
    current_path = env.get("PATH", "")
    if local_bin not in current_path.split(":"):
        env["PATH"] = f"{local_bin}:{current_path}"
    # cluster.yaml에 kubeconfig 경로가 명시된 경우, 환경변수가 없을 때만 주입
    kc = kubeconfig_path()
    if kc and "KUBECONFIG" not in env:
        env["KUBECONFIG"] = kc
    return env


def run(
    cmd: list[str] | str,
    cwd: Optional[str] = None,
    timeout: int = 120,
    shell: bool = False,
) -> dict:
    """
    일반 shell 명령 실행. smoke test 등에 사용.

    Returns:
        {"stdout": str, "stderr": str, "exit_code": int, "command": str}
    """
    command_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
            shell=shell,
            env=_env(),
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "command": command_str,
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "exit_code": -1,
            "command": command_str,
        }
    except FileNotFoundError as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
            "command": command_str,
        }


def pipe(cmd1: list[str], cmd2: list[str], cwd: Optional[str] = None, timeout: int = 120) -> dict:
    """
    cmd1의 stdout을 cmd2의 stdin으로 파이프. helm template | kubeconform 등에 사용.

    Returns:
        {"stdout": str, "stderr": str, "exit_code": int, "command": str}
    """
    command_str = " ".join(cmd1) + " | " + " ".join(cmd2)
    p1 = p2 = None
    stdout = stderr = b""
    exit_code = -1
    error_msg = None

    try:
        env = _env()
        p1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env)
        p2 = subprocess.Popen(cmd2, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env)
        p1.stdout.close()  # parent closes write end; p1 gets SIGPIPE when p2 exits
        stdout, stderr = p2.communicate(timeout=timeout)
        try:
            p1.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # p1 already sent all output; cleaned up in finally
        exit_code = p2.returncode
    except subprocess.TimeoutExpired:
        error_msg = f"Command timed out after {timeout}s"
    except FileNotFoundError as e:
        error_msg = str(e)
    finally:
        # 반드시 두 프로세스를 모두 kill+wait해서 좀비 방지
        for p in filter(None, [p2, p1]):
            if p.poll() is None:
                p.kill()
            p.wait()

    if error_msg:
        return {"stdout": "", "stderr": error_msg, "exit_code": -1, "command": command_str}
    return {
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "exit_code": exit_code,
        "command": command_str,
    }
