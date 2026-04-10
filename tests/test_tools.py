"""
harness/tools/ 클러스터 연결 테스트
실제 클러스터에서 read-only 명령만 실행
"""

import pytest
from harness.tools import kubectl, helm, shell


# ── shell ─────────────────────────────────────────────────────────────────────

def test_shell_run():
    result = shell.run(["echo", "hello"])
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]
    assert result["command"] == "echo hello"


def test_shell_timeout():
    result = shell.run(["sleep", "10"], timeout=1)
    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"]


def test_shell_not_found():
    result = shell.run(["no_such_command_xyz"])
    assert result["exit_code"] == -1


# ── kubectl ───────────────────────────────────────────────────────────────────

def test_kubectl_get_nodes():
    result = kubectl.get("nodes", namespace="default")
    assert result["exit_code"] == 0, result["stderr"]
    assert "alpha-m1" in result["stdout"]


def test_kubectl_get_pods_kube_system():
    result = kubectl.get("pods", namespace="kube-system")
    assert result["exit_code"] == 0, result["stderr"]


def test_kubectl_get_events_kagent():
    result = kubectl.get_events(namespace="kagent")
    assert result["exit_code"] == 0, result["stderr"]


def test_kubectl_describe_node():
    result = kubectl.describe("node", "alpha-m1", namespace="default")
    assert result["exit_code"] == 0, result["stderr"]
    assert "alpha-m1" in result["stdout"]


# ── helm ──────────────────────────────────────────────────────────────────────

def test_helm_list():
    """helm이 실행 가능한지 확인."""
    result = shell.run(["helm", "list", "-A"])
    assert result["exit_code"] == 0, result["stderr"]
    print(f"\n[helm list]\n{result['stdout']}")
