"""
LangGraph 그래프 조립.

노드: developer → static_verifier → (조건) → runtime_verifier → (조건) → END
인터럽트:
  - interrupt_before=["developer"]  : LLM에 넘길 컨텍스트 확인, 추가 지시 가능
  - interrupt_after=["runtime_verifier"] : 결과 확인 후 계속/중단 결정

라우팅:
  static_verifier  → pass: runtime_verifier / fail: developer
  runtime_verifier → pass: END           / fail: developer

error_count가 max_retries에 도달하면 developer 진입 전에도 interrupt를
걸어 사람이 강제 개입할 수 있게 한다 (그래프 외부, run.py에서 처리).
"""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from harness.state import HarnessState
from harness.nodes.developer import developer_node
from harness.nodes.static_verifier import static_verifier_node
from harness.nodes.runtime_verifier import runtime_verifier_node


# ── 라우팅 함수 ───────────────────────────────────────────────────────────────

def _route_after_static(state: HarnessState) -> str:
    v = state.get("static_verification", {}) or {}
    return "runtime_verifier" if v.get("passed") else "developer"


def _route_after_runtime(state: HarnessState) -> str:
    v = state.get("verification", {}) or {}
    return END if v.get("passed") else "developer"


# ── 그래프 빌더 ───────────────────────────────────────────────────────────────

def build_graph() -> "CompiledGraph":  # type: ignore[name-defined]
    """
    컴파일된 LangGraph 반환.

    interrupt_before=["developer"]:
        - developer 실행 직전 일시 정지
        - run.py가 사람 입력을 받아 state에 주입 후 resume
    interrupt_after=["runtime_verifier"]:
        - runtime_verifier 실행 직후 일시 정지
        - run.py가 결과를 출력하고 사람이 계속/중단을 결정
    """
    g = StateGraph(HarnessState)

    g.add_node("developer", developer_node)
    g.add_node("static_verifier", static_verifier_node)
    g.add_node("runtime_verifier", runtime_verifier_node)

    g.set_entry_point("developer")

    g.add_edge("developer", "static_verifier")

    g.add_conditional_edges(
        "static_verifier",
        _route_after_static,
        {"runtime_verifier": "runtime_verifier", "developer": "developer"},
    )

    g.add_conditional_edges(
        "runtime_verifier",
        _route_after_runtime,
        {END: END, "developer": "developer"},
    )

    return g.compile(
        interrupt_before=["developer"],
        interrupt_after=["runtime_verifier"],
        checkpointer=MemorySaver(),
    )
