"""
하네스 진입점.

사용법:
    python scripts/run.py --phase <phase> --sub-goal <subgoal> [옵션]

인터럽트 흐름:
    1. developer 직전 interrupt
       - 현재 state(sub_goal, context)를 출력
       - 추가 지시를 입력하면 state.user_hint에 저장 후 developer에 전달
       - 빈 입력이면 그대로 진행, 'abort'이면 중단
    2. runtime_verifier 직후 interrupt
       - 검증 결과(passed/fail, checks) 출력
       - pass: 'continue' 또는 Enter → END
       - fail: 계속 재시도할지('continue'), 중단할지('abort') 선택

error_count가 --max-retries에 도달하면 developer 직전 interrupt에서
강제 중단 또는 사람 개입을 요구한다.
"""

import argparse
import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from harness.graph import build_graph
from harness.state import HarnessState

console = Console()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="GikView 개발 하네스 — 매니페스트 작성 + 정적/런타임 검증 루프",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python scripts/run.py --phase monitoring --sub-goal prometheus
  python scripts/run.py --phase mqtt --sub-goal emqx --max-retries 5
  python scripts/run.py --phase monitoring --sub-goal grafana --skip-interrupt
        """,
    )
    parser.add_argument(
        "--phase", required=True,
        help="처리할 phase 이름 (예: monitoring, mqtt, security)",
    )
    parser.add_argument(
        "--sub-goal", required=True, dest="sub_goal",
        help="처리할 sub_goal 이름 (예: prometheus, emqx)",
    )
    parser.add_argument(
        "--max-retries", type=int, default=3, dest="max_retries",
        metavar="N",
        help="Developer 최대 재시도 횟수. 초과 시 강제 interrupt (기본값: 3)",
    )
    parser.add_argument(
        "--skip-interrupt", action="store_true", dest="skip_interrupt",
        help="interrupt 없이 자동 진행 (CI/비대화형 환경용)",
    )
    return parser.parse_args()


# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────────

def _print_header(phase: str, sub_goal: str) -> None:
    console.print(Panel(
        f"[bold]Phase[/bold]: {phase}  |  [bold]Sub-Goal[/bold]: {sub_goal}",
        title="[cyan]GikView Harness[/cyan]",
        border_style="cyan",
    ))


def _print_verification(state: HarnessState) -> None:
    v = state.get("verification") or {}
    passed = v.get("passed", False)
    stage = v.get("stage", "unknown")
    status_str = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"

    console.print(f"\n[bold]Verification Result[/bold]: {status_str}  (stage: {stage})")

    # static checks
    checks = v.get("checks", [])
    if checks:
        t = Table(box=box.SIMPLE, show_header=True)
        t.add_column("check", style="dim")
        t.add_column("status")
        t.add_column("detail")
        for c in checks:
            s = c["status"]
            color = "green" if s == "pass" else ("yellow" if s == "skip" else "red")
            t.add_row(c["name"], f"[{color}]{s}[/{color}]", c.get("detail", ""))
        console.print(t)

    # runtime phase1
    p1 = v.get("runtime_phase1", {})
    if p1:
        console.print("[bold]Runtime Phase 1[/bold]")
        t = Table(box=box.SIMPLE, show_header=True)
        t.add_column("check", style="dim")
        t.add_column("status")
        t.add_column("detail")
        for c in p1.get("checks", []):
            s = c["status"]
            color = "green" if s == "pass" else ("yellow" if s == "skip" else "red")
            t.add_row(c["name"], f"[{color}]{s}[/{color}]", c.get("detail", ""))
        console.print(t)

    # runtime phase2
    p2 = v.get("runtime_phase2", {})
    if p2:
        p2_status = "[green]pass[/green]" if p2.get("passed") else "[red]fail[/red]"
        console.print(f"[bold]Runtime Phase 2[/bold]: {p2_status}")
        for obs in p2.get("observations", []):
            console.print(f"  [dim]{obs.get('area','')}[/dim]: {obs.get('finding','')}")
        for sug in p2.get("suggestions", []):
            console.print(f"  [yellow]→ {sug}[/yellow]")


def _print_artifacts(state: HarnessState) -> None:
    art = state.get("dev_artifacts") or {}
    files = art.get("files", [])
    notes = art.get("notes", "")
    if files:
        console.print(f"\n[bold]Written files[/bold] ({len(files)}):")
        for f in files:
            console.print(f"  [green]✓[/green] {f}")
    if notes:
        console.print(f"[dim]Notes: {notes}[/dim]")


# ── interrupt 처리 ────────────────────────────────────────────────────────────

def _handle_developer_interrupt(
    state: HarnessState,
    max_retries: int,
    skip: bool,
) -> tuple[bool, str]:
    """
    developer 직전 interrupt 처리.

    Returns:
        (should_continue, user_hint)
        should_continue=False이면 run.py가 중단.
    """
    error_count = state.get("error_count", 0)
    sub_goal = state.get("current_sub_goal", {})
    verification = state.get("verification")
    is_retry = verification is not None and not verification.get("passed", True)

    # error_count가 max_retries 이상이면 강제 개입 요구
    at_limit = error_count >= max_retries

    if is_retry:
        console.print(f"\n[yellow]Retry #{error_count}[/yellow] (max: {max_retries})")
        _print_verification(state)

    console.print(Panel(
        f"[bold]Sub-Goal[/bold]: {sub_goal.get('name', '?')}  "
        f"|  [bold]Phase[/bold]: {sub_goal.get('phase', '?')}\n"
        f"[dim]error_count={error_count}[/dim]",
        title="[yellow]── Developer Interrupt ──[/yellow]",
        border_style="yellow",
    ))

    if at_limit:
        console.print(
            f"[red bold]최대 재시도({max_retries}회) 도달. 사람 개입 필요.[/red bold]"
        )
        # skip 모드여도 limit에선 반드시 확인
        choice = _prompt("계속 진행하려면 'continue', 중단하려면 'abort': ").strip().lower()
        if choice != "continue":
            return False, ""

    if skip:
        console.print("[dim]--skip-interrupt: 자동 진행[/dim]")
        return True, ""

    hint = _prompt(
        "추가 지시사항을 입력하세요 (없으면 Enter, 중단은 'abort'): "
    ).strip()

    if hint.lower() == "abort":
        return False, ""

    return True, hint


def _handle_runtime_interrupt(state: HarnessState, skip: bool) -> bool:
    """
    runtime_verifier 직후 interrupt 처리.

    Returns:
        should_continue — False이면 run.py가 중단.
    """
    _print_verification(state)

    v = state.get("verification") or {}
    passed = v.get("passed", False)

    if passed:
        console.print(Panel(
            "[green bold]검증 통과[/green bold] — END로 진행합니다.",
            border_style="green",
        ))
        if skip:
            return True
        _prompt("Enter를 누르면 종료합니다: ")
        return True

    # fail
    console.print(Panel(
        "[red bold]검증 실패[/red bold] — Developer로 돌아갈 수 있습니다.",
        border_style="red",
    ))

    if skip:
        console.print("[dim]--skip-interrupt: 자동 재시도[/dim]")
        return True

    choice = _prompt("재시도하려면 'continue', 중단하려면 'abort': ").strip().lower()
    return choice == "continue"


def _prompt(msg: str) -> str:
    """sys.stdin이 TTY일 때만 입력 받음. 비대화형이면 빈 문자열 반환."""
    if sys.stdin.isatty():
        return input(msg)
    return ""


# ── 메인 루프 ─────────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    initial_state: HarnessState = {
        "current_phase": args.phase,
        "current_sub_goal": {
            "name": args.sub_goal,
            "phase": args.phase,
            "stage": "dev",
        },
        "history": [],
        "error_count": 0,
    }

    graph = build_graph()
    config: dict[str, Any] = {
        "configurable": {"thread_id": f"{args.phase}-{args.sub_goal}"}
    }

    _print_header(args.phase, args.sub_goal)

    first_run = True   # 첫 호출만 initial_state 전달
    user_hint = ""     # developer interrupt에서 받은 추가 지시

    while True:
        # ── user_hint state 주입 (developer 재개 직전) ────────────────────────
        if user_hint:
            graph.update_state(config, {"user_hint": user_hint}, as_node="developer")
            user_hint = ""

        # ── 스트림 실행 ───────────────────────────────────────────────────────
        # 첫 실행: initial_state를 넘겨 그래프를 시작
        # 이후 실행: None을 넘겨 interrupt 지점에서 resume
        stream_input = initial_state if first_run else None
        first_run = False

        try:
            for event in graph.stream(stream_input, config, stream_mode="values"):
                _on_event(event)
        except Exception as e:
            console.print(f"[red]Runtime error: {e}[/red]")
            raise

        # ── interrupt 지점 판정 ───────────────────────────────────────────────
        snapshot = graph.get_state(config)

        # metadata.writes: 직전에 실행된 노드 → interrupt 종류 구분에 사용
        # interrupt_before["developer"]      → writes에 runtime_verifier 없음
        # interrupt_after["runtime_verifier"] → writes에 "runtime_verifier" 있음
        last_writes: dict = (snapshot.metadata or {}).get("writes", {})
        after_runtime = "runtime_verifier" in last_writes

        if after_runtime:
            # ── interrupt_after["runtime_verifier"] ───────────────────────────
            # snapshot.next: 라우팅 결과
            #   pass → ()     (END 예정, 한 번 더 stream해서 마무리)
            #   fail → ("developer",)
            should_continue = _handle_runtime_interrupt(
                snapshot.values, skip=args.skip_interrupt
            )
            if not should_continue:
                console.print("[red]중단합니다.[/red]")
                sys.exit(1)
            # pass/fail 모두 resume → 다음 루프에서 graph.stream(None) 처리
            # pass → END: resume 후 그래프 완료, 다음 루프에서 break
            # fail → resume 후 interrupt_before["developer"] 발생, 다음 루프에서 처리

        elif not snapshot.next:
            # ── 진짜 그래프 종료 (END) ────────────────────────────────────────
            console.print("\n[green bold]✓ 완료[/green bold]")
            _print_artifacts(snapshot.values)
            break

        elif snapshot.next[0] == "developer":
            # ── interrupt_before["developer"] ────────────────────────────────
            should_continue, hint = _handle_developer_interrupt(
                snapshot.values,
                max_retries=args.max_retries,
                skip=args.skip_interrupt,
            )
            if not should_continue:
                console.print("[red]중단합니다.[/red]")
                sys.exit(1)
            user_hint = hint

        else:
            console.print(f"[yellow]Unexpected interrupt: next={snapshot.next}[/yellow]")
            break


def _on_event(event: dict) -> None:
    """스트림 이벤트에서 진행 상황 출력."""
    sub_goal = event.get("current_sub_goal", {})
    stage = sub_goal.get("stage", "")
    name = sub_goal.get("name", "")

    if stage == "dev":
        console.print(f"[cyan]⟳[/cyan]  Developer  [{name}]")
        _print_artifacts(event)
    elif stage == "static_verify":
        v = event.get("verification") or {}
        status = "[green]pass[/green]" if v.get("passed") else "[red]fail[/red]"
        console.print(f"[cyan]⟳[/cyan]  Static Verifier  [{name}]  {status}")
    elif stage == "runtime_verify":
        v = event.get("verification") or {}
        status = "[green]pass[/green]" if v.get("passed") else "[red]fail[/red]"
        console.print(f"[cyan]⟳[/cyan]  Runtime Verifier  [{name}]  {status}")


if __name__ == "__main__":
    main()
