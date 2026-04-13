"""
LangGraph 그래프 조립.

노드: developer → static_verifier → (조건) → runtime_verifier → (자가 루프 or END)
인터럽트:
  - interrupt_before=["developer"]       : LLM에 넘길 컨텍스트 확인, 추가 지시 가능
  - interrupt_after=["runtime_verifier"] : 결과 확인 후 계속/중단 결정

라우팅:
  static_verifier  → pass: runtime_verifier / fail: developer
  runtime_verifier → pass: END / fail: runtime_verifier (자가 루프)

runtime_verifier 자가 루프는 runtime_retry_count를 증가시키며 재시도.
max_runtime_retries 초과 시 run.py에서 강제 사람 개입 요구.
"""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from harness.state import HarnessState
from harness.nodes.developer import developer_node
from harness.nodes.static_verifier import static_verifier_node
from harness.nodes.runtime_verifier import runtime_verifier_node

# ── 노드 이름 상수 — run.py 등 외부에서 import해 raw string 의존 제거 ──────────
NODE_DEVELOPER = "developer"
NODE_STATIC_VERIFIER = "static_verifier"
NODE_RUNTIME_VERIFIER = "runtime_verifier"


# ── 라우팅 함수 ───────────────────────────────────────────────────────────────

def _route_after_static(state: HarnessState) -> str:
    v = state.get("static_verification", {}) or {}
    return NODE_RUNTIME_VERIFIER if v.get("passed") else NODE_DEVELOPER


def _route_after_runtime(state: HarnessState) -> str:
    v = state.get("verification", {}) or {}
    return END if v.get("passed") else NODE_RUNTIME_VERIFIER


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

    g.add_node(NODE_DEVELOPER, developer_node)
    g.add_node(NODE_STATIC_VERIFIER, static_verifier_node)
    g.add_node(NODE_RUNTIME_VERIFIER, runtime_verifier_node)

    g.set_entry_point(NODE_DEVELOPER)

    g.add_edge(NODE_DEVELOPER, NODE_STATIC_VERIFIER)

    g.add_conditional_edges(
        NODE_STATIC_VERIFIER,
        _route_after_static,
        {NODE_RUNTIME_VERIFIER: NODE_RUNTIME_VERIFIER, NODE_DEVELOPER: NODE_DEVELOPER},
    )

    g.add_conditional_edges(
        NODE_RUNTIME_VERIFIER,
        _route_after_runtime,
        {END: END, NODE_RUNTIME_VERIFIER: NODE_RUNTIME_VERIFIER},
    )

    return g.compile(
        interrupt_before=[NODE_DEVELOPER],
        interrupt_after=[NODE_RUNTIME_VERIFIER],
        checkpointer=MemorySaver(),
    )
