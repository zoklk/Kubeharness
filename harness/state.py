from typing import TypedDict, Optional, Literal

Stage = Literal["dev", "static_verify", "runtime_verify"]

class _SubGoalRequired(TypedDict):
    name: str          # 예: "prometheus" — CLI --sub-goal 인수와 일치
    phase: str         # 예: "monitoring"
    stage: Stage       # 기록용

class SubGoal(_SubGoalRequired, total=False):
    service_name: str  # phase doc의 service_name 필드 (없으면 name으로 폴백)

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
    error_count: int

    # 사람 추가 지시 (developer interrupt에서 입력, developer_node가 user message에 포함 후 소거)
    user_hint: Optional[str]

    # 컨텍스트 캐시 (각 노드가 context/ 로드 후 저장, 하위 노드에서 재사용)
    sub_goal_spec: Optional[str]  # context/phases/<phase>.md에서 추출한 현재 sub_goal 섹션
    technology_name: str           # sub_goal spec의 **technology**: 필드. 없으면 service_name 폴백