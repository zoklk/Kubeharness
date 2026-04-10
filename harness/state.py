from typing import TypedDict, Optional, Literal

Stage = Literal["dev", "static_verify", "runtime_verify"]

class SubGoal(TypedDict):
    name: str          # 예: "prometheus"
    phase: str         # 예: "monitoring"
    stage: Stage       # 기록용

class HarnessState(TypedDict, total=False):
    # 위치
    current_phase: str
    current_sub_goal: SubGoal

    # 산출물
    dev_artifacts: Optional[dict]  # {"files": [...], "notes": "..."}

    # 검증 결과
    static_verification: Optional[dict]
    runtime_verification: Optional[dict]

    # 통합 결과 (재시도 판단용)
    verification: Optional[dict]

    # 이력
    history: list[dict]
    error_count: int