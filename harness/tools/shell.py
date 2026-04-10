    import subprocess
    from typing import Optional


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
