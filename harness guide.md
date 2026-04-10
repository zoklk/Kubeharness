# Claude Code Implementation Guide — Development Harness

이 문서는 Claude Code가 본 하네스를 구현할 때 따라야 할 지침서다. 설계는 확정되어 있으니 **설계 재논의 없이 구현에 집중**할 것.

## 목표

> "정의된 계획 및 목표 상태에 따라 자동으로 코드를 작성 및 평가하는 루프"

LangGraph 기반 개발용 하네스. 한 번의 invocation으로 **하나의 sub_goal**을 처리: 매니페스트 작성 → 정적 검증 → 배포 + 동적 검증 → 종료. 다음 sub_goal은 사람이 새 invocation으로 명시적 호출.

**범위 밖**: rpi4 엣지 배포, argocd, git 자동화, 트러블슈팅 루프, 멀티 sub_goal 자동 진행.

## 핵심 용어

- **phase**: 프로젝트 단위. 예: `mqtt`, `monitoring`, `security`, `edgegateway`
- **sub_goal**: phase 내 작업 단위. 예: `prometheus`, `grafana`, `cilium-lb`. 1 invocation = 1 sub_goal
- **stage**: sub_goal lifecycle 위치. State에 기록용으로만 존재, 라우팅에 사용 안 함

## 노드 구성

| 노드 | 종류 | LLM | 역할 |
|---|---|---|---|
| Developer | 작성 | O | 매니페스트/차트 작성. kagent MCP read-only tool로 cluster 상태 조회 가능 |
| Static Verifier | 검증 | X | 정적 검사. 결정적 |
| Runtime Verifier | 검증 | O (+결정적 게이트) | 결정적 게이트로 배포·기본 검증, LLM으로 추가 진단 |

LLM 사용 노드: Developer, Runtime Verifier 둘. Static Verifier는 순수 Python.

## 전체 흐름

```
사람: python scripts/run.py --phase <phase> --sub-goal <subgoal>
  ↓
하네스: context/phases/<phase>.md에서 sub_goal 명세 추출
  ↓
Developer (LLM + kagent MCP read-only)
  - 시스템 프롬프트: context/developer_prompt.md
  - 첨부: conventions.md, tech_stack.md, sub_goal 명세, (재시도 시) verification 결과
  - kagent MCP read-only tool 사용 가능: k8s_get_resources, k8s_get_pod_logs, k8s_get_events 등
  - 산출물: edge-server/helm/<service>/ 또는 edge-server/manifests/<service>/
  ↓
(interrupt: 사람 확인)
  ↓
Static Verifier (Python, 결정적)
  - yamllint
  - kubeconform (또는 helm template + kubeconform)
  - helm lint
  - trivy config
  - gitleaks
  - kubectl/helm dry-run --server (immutable 충돌 사전 감지)
  ↓
  fail → Developer 복귀
  pass → 다음
  ↓
Runtime Verifier
  ┌─ Phase 1 (Python, 결정적 게이트) ─┐
  │  ① helm upgrade --install        │
  │  ② kubectl wait --for=Ready      │
  │  ③ kubectl get events (Warning)  │
  │  ④ smoke test 스크립트 실행      │
  │     (sub_goal에 명시되어 있으면) │
  └────────────────────────────────────┘
  ↓
  Phase 1 fail → 즉시 fail 종료, Phase 2 skip → Developer 복귀
  Phase 1 pass → Phase 2 진행
  ↓
  ┌─ Phase 2 (LLM + kagent MCP read-only) ─┐
  │  추가 진단: 미묘한 문제, 로그 패턴,   │
  │  events 의미 분석                     │
  │  출력: 강제 JSON schema               │
  └────────────────────────────────────────┘
  ↓
(interrupt: 사람 확인)
  ↓
종합 판정:
  - Phase 1 pass + Phase 2 passed=true → END (성공)
  - Phase 1 pass + Phase 2 passed=false → fail, suggestions를 사람에게 노출
  - Phase 1 fail → fail
  ↓
fail → Developer 복귀 (verification 첨부)
pass → END
```

**Runtime Verifier LLM은 매니페스트 수정 권한 없음**. 쓰기 tool은 노출하지 않음. 진단과 자연어 제안만 출력.

## State 스키마 (`harness/state.py`)

```python
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
```

### `verification` 형식 (통합)

```python
{
    "passed": bool,
    "stage": "static" | "runtime",        # 어디서 fail했는지
    "checks": [
        {"name": "yamllint", "status": "pass|fail|skip", "detail": "...", "log_path": "..."},
        ...
    ],
    "runtime_phase1": {                    # runtime 단계까지 갔으면
        "passed": bool,
        "checks": [...]
    },
    "runtime_phase2": {                    # phase1 통과했을 때만
        "passed": bool,
        "observations": [...],
        "suggestions": [...]
    },
    "log_dir": "logs/raw/<timestamp>/"
}
```

## 디렉토리 구조

```
GikView/
├── harness/
│   ├── __init__.py
│   ├── graph.py                    # 3-node loop + interrupt
│   ├── state.py
│   │
│   ├── nodes/
│   │   ├── developer.py            # LLM + kagent MCP read-only
│   │   ├── static_verifier.py      # 결정적
│   │   └── runtime_verifier.py     # 결정적 게이트 + LLM 진단
│   │
│   ├── verifiers/
│   │   ├── static.py               # yamllint, kubeconform, helm lint, trivy, gitleaks, server dry-run
│   │   └── runtime_gates.py        # helm install, kubectl wait, events, smoke test
│   │
│   ├── tools/                      # subprocess 래퍼
│   │   ├── kubectl.py
│   │   ├── helm.py
│   │   └── shell.py
│   │
│   ├── mcp/
│   │   └── kagent_client.py        # kagent MCP server client + tool 화이트리스트
│   │
│   └── llm/
│      └── client.py               # provider 추상화 (Anthropic / OpenAI compat)
│
├── context/                        # 사람이 작성. read-only
│   ├── overview.md                 # 사람용. 프로젝트 목적, 아키텍처
│   ├── tech_stack.md               # 사람 + 모델용. 버전 포함
│   ├── conventions.md              # 사람 + 모델용. k8s/helm 컨벤션
│   ├── developer_prompt.md         # 모델용. Developer 시스템 프롬프트
│   ├── runtime_verifier_prompt.md  # 모델용. Phase 2 LLM 시스템 프롬프트
│   └── phases/
│       └── <phase>.md              # 모델용. sub_goal별 인터페이스 사양 + 완성 기준
|
├── logs/
│   └── raw/                        # 전체 로그 파일
│       └── <timestamp>/
│
├── scripts/
│   └── run.py                      # 진입점
│
├── config/
│   ├── cluster.yaml                # kubeconfig 경로, namespace=gikview
│   ├── kagent.yaml                 # MCP server URL, 허용 tool 목록
│   └── llm.yaml                    # provider, base_url, model, temperature
│
├── tests/
│   ├── test_static.py
│   ├── test_runtime_gates.py
│   ├── test_kagent_client.py
│   └── test_llm_client.py
|
├── edge-server/                      # Developer 산출물 + 사람이 미리 작성한 smoke test
│   ├── helm/<service>/
│   ├── manifests/<service>/
│   └── scripts/
│       └── smoke-test-<service>.sh
│
│
├── pyproject.toml
├── README.md
└── .gitignore
```
본 하네스는 GikView 프로젝트의 메인 레포 안에서 동작한다. 즉:
- `harness/`: 하네스 코드
- `edge-server/`: 하네스가 작성하는 매니페스트/차트. 추후 엣지 배포 시 그대로 사용
- `context/`, `config/`: 하네스 입력
- `tests/`: 하네스 테스트

`edge-server/`라는 이름은 **이 매니페스트들의 최종 목적지가 엣지 서버**임을 나타낸다. 본 하네스 단계에서는 연구실 클러스터(`gikview` 네임스페이스)에 배포해 검증만 하지만, 산출물 자체는 엣지 배포용이다.

**하네스 자기참조 방지**:
- Developer는 `edge-server/` 외부 파일 작성 금지
- Developer 노드에 경로 prefix 가드 구현 (`edge-server/`로 시작하지 않으면 reject)
- Developer 시스템 프롬프트에도 명시
- Static Verifier가 dev_artifacts.files를 검사해 위반 시 fail


## 컨벤션 의존성 (Runtime Verifier 결정적 게이트의 전제)

Runtime Verifier가 LLM 없이 결정적으로 동작하려면 다음이 컨벤션으로 강제되어야 함. 이 정보들은 sub_goal 이름만 알면 도출 가능해야 함:

| 항목 | 도출 규칙 |
|---|---|
| namespace | `gikview` 고정 |
| chart_path | `edge-server/helm/<service>/` |
| values_files | `values.yaml` + `values-dev.yaml` |
| release_name | `<service>-dev-v1` (사람이 변경 시 sub_goal 명세에 명시, "dev는 개발 환경 식별자") |
| label_selector | `app.kubernetes.io/name=<service>` |
| smoke_test_path | `edge-server/scripts/smoke-test-<service>.sh` (없으면 skip) |

→ Runtime Verifier 결정적 게이트 함수 시그니처:
```python
def run_runtime_phase1(service_name: str) -> dict:
    namespace = "gikview"
    chart_path = f"edge-server/helm/{service_name}"
    values_files = [f"{chart_path}/values.yaml", f"{chart_path}/values-dev.yaml"]
    release_name = f"{service_name}-dev-v1"
    label_selector = f"app.kubernetes.io/name={service_name}"
    smoke_test = Path(f"edge-server/scripts/smoke-test-{service_name}.sh")
    # ... 위 정보로 helm install, kubectl wait, events, smoke test 실행
```

이 컨벤션은 `context/conventions.md`에 명시. Developer가 매니페스트 작성 시 같은 컨벤션을 따라야 Runtime Verifier가 동작.

## 구현 순서 (bottom-up)

### Step 1: State (`harness/state.py`)

위 스키마 그대로 구현.

### Step 2: Subprocess tool 래퍼 (`harness/tools/`)

- `kubectl.py`: `apply`, `get`, `wait`, `dry_run_server`, `get_events`, `get_endpoints`, `describe`
- `helm.py`: `lint`, `template`, `upgrade_install`, `dry_run_server`
- `shell.py`: 일반 shell 명령 (smoke test 실행 등)

각 함수 반환:
```python
{
    "stdout": str,
    "stderr": str,
    "exit_code": int,
    "command": str,  # 디버깅용
}
```

**LLM 호출 없음**. 순수 subprocess.

### Step 3: LLM 클라이언트 (`harness/llm/client.py`)

provider 추상화. `config/llm.yaml`로 분기:

```yaml
# config/llm.yaml
provider: anthropic   # 또는 openai_compat
endpoint: ""          # openai_compat일 때만
api_key_env: ANTHROPIC_API_KEY
model: claude-sonnet-4-20250514
temperature: 0.1
```

함수 시그니처:
```python
def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    response_format: dict | None = None,  # JSON schema 강제용
) -> dict:
    """
    Returns:
        {
            "content": str,
            "tool_calls": list[dict] | None,
            "raw": <provider response>
        }
    """
```

내부 구현:
- `provider == "anthropic"`: anthropic SDK 사용
- `provider == "openai_compat"`: openai SDK + base_url로 로컬 모델 (gpt-oss-120b 등)

재시도: 3회, exponential backoff. 네트워크 에러만 재시도, JSON 파싱 실패는 즉시 fail.

**중요**: 처음엔 `provider: anthropic`으로 시작. 연구실 모델 준비되면 yaml만 변경. 단, 모델 교체 시 프롬프트 동작 차이 가능성 있음 — 교체 시점에 프롬프트 재튜닝 시간 별도 확보.

### Step 4: kagent MCP 클라이언트 (`harness/mcp/kagent_client.py`)

연구실 클러스터에 kagent를 설치하면 `kagent-tool-server` MCP server가 자동으로 뜸. 이 서버에 HTTP/JSON-RPC로 연결.

**권장 구현 방식**: `langchain-mcp-adapters` 패키지 사용. kagent MCP server의 모든 tool을 LangChain tool로 자동 변환:

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

async def get_kagent_tools(allowed_names: list[str] | None = None):
    client = MultiServerMCPClient({
        "kagent": {
            "url": "<config/kagent.yaml의 url>",
            "transport": "streamable_http",
        }
    })
    tools = await client.get_tools()
    if allowed_names is not None:
        tools = [t for t in tools if t.name in allowed_names]
    return tools
```

**Tool 화이트리스트** (`config/kagent.yaml`):

```yaml
url: http://kagent-tool-server.kagent.svc.cluster.local:80/mcp
readonly_tools:
  - k8s_get_resources
  - k8s_get_pod_logs
  - k8s_get_events
  - k8s_get_resource_yaml
  - k8s_describe_resource
  - k8s_get_available_api_resources
  - k8s_get_cluster_configuration
  # helm read tool 등 추가
```

**Developer와 Runtime Verifier(Phase 2) 모두 readonly_tools만 사용**. 쓰기 tool은 절대 노출 금지. 노출 안 된 tool은 LLM이 호출 못 함.

**사전 확인 필요**: kagent 설치 후 실제 tool 목록을 다음 명령으로 확인:
```bash
kubectl port-forward -n kagent svc/kagent-tool-server 8080:80
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```
응답에서 readonly tool 이름을 골라 yaml에 채움.

### Step 5: 정적 검증 함수 (`harness/verifiers/static.py`)

각 함수는 `{"name", "status": "pass|fail|skip", "detail", "log_path"}` 반환. **LLM 없음**.

구현:
- `check_yamllint(path)`: `yamllint` CLI 호출
- `check_kubeconform(path)`: `kubeconform -strict` CLI 호출
- `check_helm_lint(chart_path)`: `helm lint` CLI 호출
- `check_helm_template_kubeconform(chart_path, values_files)`: `helm template` 출력을 `kubeconform`에 파이프
- `check_trivy_config(path)`: `trivy config` CLI 호출
- `check_gitleaks(path)`: `gitleaks detect` CLI 호출
- `check_helm_dry_run_server(chart_path, values_files, release_name, namespace)`: `helm upgrade --install --dry-run=server`. immutable 충돌 사전 감지
- `check_kubectl_dry_run_server(manifest_path, namespace)`: `kubectl apply --dry-run=server`. helm 미사용 시

각 체크는 독립적. 하나가 fail이어도 다음 체크 계속 실행 (전체 이슈를 한 번에 보여주기 위해).

### Step 6: 런타임 게이트 함수 (`harness/verifiers/runtime_gates.py`)

`run_runtime_phase1(service_name: str) -> dict`:

순서대로 실행, 하나가 fail이면 이후는 skip 처리하고 즉시 반환:

1. `helm upgrade --install <release> <chart> -n gikview -f values.yaml -f values-dev.yaml`
   - exit_code != 0 → fail
   - immutable 충돌은 stderr에 명시됨, fail로 처리하고 사람에게 노출
2. `kubectl wait --for=condition=Ready pods -l app.kubernetes.io/name=<service> -n gikview --timeout=120s`
   - timeout → fail
3. `kubectl get events -n gikview --field-selector type=Warning,involvedObject.kind=Pod -o json` 후 최근 5분 이내 warning만 필터
   - warning 존재 → fail (warning 내용을 detail에 포함)
4. smoke test 실행 (`edge-server/scripts/smoke-test-<service>.sh` 존재 시)
   - exit_code != 0 → fail

반환:
```python
{
    "passed": bool,
    "checks": [
        {"name": "helm_install", "status": "pass|fail|skip", "detail": "...", "log_path": "..."},
        ...
    ]
}
```

### Step 7: Static Verifier 노드 (`harness/nodes/static_verifier.py`)

처리 순서:
1. dev_artifacts.files를 기반으로 helm 차트 경로와 매니페스트 파일 식별
2. 정적 체크 함수들을 모두 실행
3. 결과를 `static_verification` 필드에 저장
4. 모든 fail 체크의 로그를 `logs/raw/<timestamp>/static/` 에 저장
5. 통합 `verification` 필드 업데이트:
   - `passed`: 모든 정적 체크 pass 여부
   - `stage`: "static"

**LLM 호출 없음**.

### Step 8: Runtime Verifier 노드 (`harness/nodes/runtime_verifier.py`)

처리 순서:
1. **Phase 1 (결정적)**: `run_runtime_phase1(service_name)` 호출. service_name은 `current_sub_goal.name` 사용
2. Phase 1 fail이면:
   - `runtime_verification.runtime_phase1.passed = False`
   - 통합 `verification.passed = False`, `stage = "runtime"`
   - Phase 2 skip
   - 반환
3. Phase 1 pass면 **Phase 2 (LLM)** 진행:
   - `context/runtime_verifier_prompt.md` 시스템 프롬프트 로드
   - kagent MCP read-only tool 첨부 (langchain-mcp-adapters로)
   - 사용자 메시지: sub_goal 명세 + Phase 1 결과 요약
   - LLM 호출, JSON 응답 강제 (`response_format` 또는 schema 명시)
   - 응답 파싱:
     ```json
     {
       "passed": true,
       "observations": [
         {"area": "pod", "finding": "..."},
         {"area": "events", "finding": "..."},
         {"area": "logs", "finding": "..."}
       ],
       "suggestions": ["..."]
     }
     ```
   - 파싱 실패 → Phase 2 fail로 처리, suggestions에 "LLM response parse failed" 기록
4. 통합 결과:
   - Phase 1 pass + Phase 2 passed=true → `verification.passed = True`
   - Phase 1 pass + Phase 2 passed=false → `verification.passed = False`, suggestions를 사람에게 노출
5. 모든 결과 로그 저장

**Phase 2의 LLM은 read-only tool만**. 쓰기 작업은 절대 금지. 시스템 프롬프트에도 명시.

### Step 9: Developer 노드 (`harness/nodes/developer.py`)

- 시스템 프롬프트: `context/developer_prompt.md`
- 사용자 메시지에 첨부할 컨텍스트:
  - `context/conventions.md`
  - `context/tech_stack.md`
  - `context/phases/<current_phase>.md`에서 `current_sub_goal.name`에 해당하는 sub_goal 부분
  - 재시도 시: `verification` 결과 (실패 체크와 detail)
- kagent MCP read-only tool 첨부 (langchain-mcp-adapters)
- LLM 호출 (multi-turn tool calling 가능)
- 최종 응답에서 파일 작성 정보 추출:
  - LLM이 JSON 형식으로 응답하도록 프롬프트에 지시
  - 형식:
    ```json
    {
      "files": [
        {"path": "edge-server/helm/prometheus/Chart.yaml", "content": "..."},
        {"path": "edge-server/helm/prometheus/values.yaml", "content": "..."}
      ],
      "notes": "..."
    }
    ```
- Python 코드가 파일 시스템에 직접 씀 (LLM이 직접 파일 조작 안 함)
- `dev_artifacts` 반환: `{"files": [...], "notes": "..."}`

**Developer는 cluster 쓰기 tool 사용 금지**. tool 화이트리스트가 read-only이므로 자동으로 차단됨.

### Step 10: Graph 조립 (`harness/graph.py`)

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

def route_after_static(state):
    v = state.get("static_verification", {})
    return "runtime_verifier" if v.get("passed") else "developer"

def route_after_runtime(state):
    v = state.get("verification", {})
    return END if v.get("passed") else "developer"

def build_graph():
    g = StateGraph(HarnessState)

    g.add_node("developer", developer_node)
    g.add_node("static_verifier", static_verifier_node)
    g.add_node("runtime_verifier", runtime_verifier_node)

    g.set_entry_point("developer")

    g.add_edge("developer", "static_verifier")

    g.add_conditional_edges(
        "static_verifier",
        route_after_static,
        {"runtime_verifier": "runtime_verifier", "developer": "developer"}
    )

    g.add_conditional_edges(
        "runtime_verifier",
        route_after_runtime,
        {END: END, "developer": "developer"}
    )

    return g.compile(
        interrupt_before=["developer"],         # task 확인용 (1회차는 sub_goal 시작 확인)
        interrupt_after=["runtime_verifier"],   # 결과 확인 후 종료/재시도 결정
        checkpointer=MemorySaver()
    )
```

**재시도 흐름**: Verifier fail → Developer로 돌아감 → State의 `error_count` 증가 → 3회 도달 시 사람에게 강제 interrupt.

### Step 11: 진입점 (`scripts/run.py`)

```python
import argparse
from harness.graph import build_graph
from harness.state import HarnessState

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--sub-goal", required=True)
    args = parser.parse_args()

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
    config = {"configurable": {"thread_id": f"{args.phase}-{args.sub_goal}"}}

    # interrupt 처리 루프
    for event in graph.stream(initial_state, config):
        # event 출력, interrupt 시 사람 입력 받음
        ...

if __name__ == "__main__":
    main()
```

interrupt 처리 시 사람이 "continue" / "abort" 선택. 필요하면 state를 직접 수정 후 resume.

### Step 12: 컨텍스트 파일 작성 (사람 책임이지만 템플릿 제공)

Claude Code는 다음 파일들의 **빈 템플릿만** 생성하고, 내용은 사람이 채움:

- `context/overview.md`
- `context/tech_stack.md`
- `context/conventions.md`
- `context/developer_prompt.md`
- `context/runtime_verifier_prompt.md`
- `context/phases/_template.md`

각 파일에는 "이 파일에 무엇을 적어야 하는지" 주석만 포함.

## 핵심 원칙 (구현 중 반드시 준수)

1. **LLM 사용 노드는 Developer, Runtime Verifier(Phase 2)뿐**. Static Verifier와 Runtime Verifier(Phase 1)은 순수 Python
2. **쓰기 tool은 LLM에게 노출 금지**. kagent MCP에서 read-only 화이트리스트만 사용
3. **매니페스트 작성 권한은 Developer만**. Runtime Verifier는 진단과 자연어 제안만, 파일 수정 안 함
4. **Runtime Verifier Phase 1 우선**. Phase 1 fail이면 Phase 2 skip
5. **Phase 2 LLM 출력은 JSON schema 강제**. 파싱 실패는 fail로 간주
6. **컨벤션으로 helm/kubectl 인자 자동 도출**. service name만 알면 충분
7. **재배포는 helm upgrade --install로 끝**. delete + recreate 하지 않음. immutable 충돌은 fail로 사람에게 알림
8. **Context 디렉토리 read-only**. 하네스 코드는 context/에 쓰지 않음
9. **Developer는 `edge-server/` 내부에만 파일을 쓴다**. `harness/`, `context/`, `config/`, `tests/`, `scripts/` 등 다른 디렉토리 작성 금지. 코드 레벨 가드와 시스템 프롬프트로 강제
10. **State는 휘발성**. 영속 데이터는 raw 로그 파일
11. **에러 룰 매칭은 범위 밖**. SQLite 기록만 (선택)
12. **git/PR 자동화는 범위 밖**. 사람이 수동
13. **엣지 배포(argocd) 범위 밖**

## 테스트 전략

- **static.py 단위 테스트**: 가짜 매니페스트 파일 → 각 체크 함수가 올바른 status 반환
- **runtime_gates.py 테스트**: subprocess를 mock하여 명령 구성 검증, 결과 파싱 검증
- **kagent_client.py 테스트**: MCP server를 mock하여 tool 화이트리스트 필터링 동작 확인
- **llm/client.py 테스트**: 두 provider 모두 동일 인터페이스로 응답하는지
- **graph 통합 테스트**: 가짜 LLM client + 가짜 클러스터로 end-to-end 흐름 확인

## 의존성 (`pyproject.toml`)

```toml
[project]
dependencies = [
    "langgraph",
    "langchain-core",
    "langchain-mcp-adapters",      # kagent MCP 연결
    "anthropic",                    # 1차 LLM provider
    "openai",                       # 연구실 모델 (OpenAI compat)
    "pydantic",
    "pyyaml",
    "rich",                         # CLI 출력
]

[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-asyncio",
    "ruff",
    "mypy",
]
```

시스템 CLI (pip 불가, 사전 설치):
- kubectl, helm
- yamllint, kubeconform, trivy, gitleaks
- bash, jq

## 사전 검증 사항 (구현 전 확인)

이 두 가지는 본격 구현 전에 반드시 확인:

### 1. LLM tool calling 동작 확인
초기엔 Anthropic Claude로 진행하므로 문제 없음. 연구실 모델로 교체 시 다음 테스트:

```python
from openai import OpenAI
client = OpenAI(base_url="http://10.40.40.40:8000/v1", api_key="dummy")
response = client.chat.completions.create(
    model="openai/gpt-oss-120b",
    messages=[{"role": "user", "content": "Get pods in gikview namespace"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "k8s_get_resources",
            "parameters": {"type": "object", "properties": {"resource_type": {"type": "string"}}}
        }
    }]
)
print(response.choices[0].message.tool_calls)
```

`tool_calls`가 응답에 포함되면 OK. 안 되면 모델 교체 또는 서빙 레이어 변경 필요.

### 2. kagent MCP server tool 목록 확인
kagent 설치 후:
```bash
kubectl port-forward -n kagent svc/kagent-tool-server 8080:80
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

응답의 tool 목록에서 read-only 후보를 골라 `config/kagent.yaml`의 `readonly_tools`에 채움.

## 체크리스트 (구현 완료 후 확인)

- [ ] State에 `current_stage` 필드 없음, SubGoal 내부의 stage만 존재
- [ ] LLM 호출은 Developer와 Runtime Verifier(Phase 2)에서만
- [ ] Static Verifier에 LLM 호출 없음
- [ ] Runtime Verifier Phase 1에 LLM 호출 없음
- [ ] Runtime Verifier Phase 1 fail 시 Phase 2 skip
- [ ] Phase 2 LLM 응답이 JSON schema로 파싱됨
- [ ] kagent MCP client가 readonly_tools만 노출
- [ ] Developer가 edge-server/ 외부에 파일 쓰지 않음
- [ ] Runtime Verifier가 매니페스트 파일을 수정하지 않음
- [ ] llm/client.py가 anthropic / openai_compat 두 provider 지원
- [ ] config/llm.yaml만 변경하면 provider 교체 가능
- [ ] Static Verifier에 helm dry-run --server 포함 (immutable 충돌 사전 감지)
- [ ] Runtime Verifier가 재시도 시 helm upgrade --install로 멱등 배포
- [ ] interrupt가 Developer 앞과 Runtime Verifier 뒤에 걸림
- [ ] `scripts/run.py --phase X --sub-goal Y` 단일 invocation으로 end-to-end 동작
- [ ] error_count 3회 도달 시 사람 개입 interrupt
- [ ] Developer 노드에 edge-server/ 경로 prefix 가드 구현
- [ ] Static Verifier가 dev_artifacts.files의 경로 prefix 검증

## 구현 중 하지 말 것

- Context 디렉토리 자동 수정
- Developer / Runtime Verifier(Phase 2) 외 노드에서 LLM 호출
- kagent MCP의 쓰기 tool 노출 (k8s_apply, k8s_patch_resource, k8s_label_resource 등)
- Runtime Verifier가 매니페스트 파일 수정
- helm uninstall 자동 호출
- delete + recreate 자동 처리
- git 작업 (commit, push, PR)
- argocd 또는 엣지 배포 코드
- 에러 룰 yaml 매칭 로직
- 멀티 sub_goal 자동 진행
- 워크스페이스 외부 파일 작성

## 구현 시 우선순위

전부 한 번에 구현하지 말고 다음 순서로 작은 단위 검증 권장:

1. **llm/client.py + 단위 테스트** (Anthropic provider만)
2. **tools/ subprocess 래퍼 + 단위 테스트**
3. **verifiers/static.py + 단위 테스트**
4. **verifiers/runtime_gates.py + 단위 테스트** (실제 클러스터 필요, 미니멀 테스트만)
5. **mcp/kagent_client.py + 연결 확인**
6. **nodes/static_verifier.py** (LLM 없음, 가장 단순)
7. **nodes/runtime_verifier.py** Phase 1만 먼저 구현
8. **nodes/runtime_verifier.py** Phase 2 추가
9. **nodes/developer.py**
10. **graph.py + scripts/run.py** end-to-end 통합

각 단계마다 동작 확인하고 다음으로. Step 1-4까지는 클러스터 없이 가능. Step 5부터 클러스터 필요.

설계에 의문이 생기면 먼저 이 문서를 다시 읽을 것. 그래도 명확하지 않으면 사람에게 질문할 것. 임의 판단으로 설계 변경 금지.
