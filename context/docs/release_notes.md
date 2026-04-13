# GikView Harness Release Notes

---

## v1.1.0

**Branch**: `harness/semi-autonomous`

### Breaking Changes

- **Manifest deployment path removed.** `edge-server/manifests/<service>/` + `kubectl apply` 경로가 완전 제거됨. 모든 배포는 Helm chart 기반이어야 함. LLM이 `manifests/` 경로로 파일을 출력하면 harness가 자동으로 drop 처리함 (에러 없음, 조용히 무시).
- **`_ALLOWED_WRITE_SUBDIRS`**: `("helm/", "docker/", "ebpf/")` — `manifests/` 제거.

### New Features

#### Runtime Verifier Self-loop with Direct File Writes (`6a02206`)
Phase 2 LLM이 진단 결과를 `suggestions`만 반환하던 것에서 **파일 직접 수정(`files` 필드)**으로 변경. harness가 파일 쓰기 후 자동으로 Phase 1 재실행 (developer 노드로 돌아가지 않음). Runtime retry는 `runtime_retry_count`로 독립 추적.

#### Profile-driven LLM Config (`69d176e`)
`config/llm_profiles.yaml` 기반 노드별 모델/파라미터 분리:
- `developer`: 기본 개발 프로필
- `runtime_verifier_phase2`: Phase 2 전용 (Claude Sonnet, web search 옵션)
- `gemma-verifier`: Google Search grounding 지원 Gemma 프로필 (`3018912`)

#### Phase 2 Web Search (`57ebbdd`)
`runtime_verifier_phase2` 프로필에 `web_search` 설정이 있으면 Phase 2 LLM에 `web_search` 도구 자동 주입. hypothesis-driven 검색 절차 프롬프트 강제.

#### CRD-only Chart Detection (`bb3cab5`)
`helm template` 출력을 분석해 Deployment/StatefulSet/DaemonSet 등 workload 리소스가 없으면 `kubectl wait`을 자동 skip. CRD-only chart(Cilium L2LB policy, operator CRD 등)에서 불필요한 300s 대기 제거.

#### failure_source Classification (`ce50e03`, `a2bee23`)
Phase 2 응답에 `failure_source` 필드 추가:
- `"implementation"`: 배포 코드/설정 오류
- `"smoke_test"`: smoke test 스크립트 자체 버그 → 자동 루프 중단, 사람 개입 강제
- `"environment"`: 클러스터/DNS/네트워크 외부 문제

`failure_source = "smoke_test"` 시 `files: []` 강제 + 루프 즉시 종료.

#### Knowledge Injection into Phase 2 (`c63cd29`)
`context/knowledge/<tech>.md` 파일을 Phase 2 user message에도 주입. Phase 1만 알던 기술 지식을 Phase 2 LLM도 참조 가능.

#### JSON Re-request Fallback (`fda1a5c`)
LLM 응답이 JSON 파싱 실패 시 별도 re-request turn으로 JSON 재요청. developer와 runtime_verifier 양쪽에 적용. `harness/llm/json_utils.py`에 `extract_json_dict()` 공통 유틸 추가.

#### Centralized JSON Parsing & Tool Loading (`69d176e`)
- `harness/llm/json_utils.py`: `extract_json_dict()` 공통화
- `harness/llm/tool_loop.py`: `run_tool_loop()`, `request_json_response()` 공통화
- `harness/mcp/kagent_client.py`: `load_node_tools()` 공통화

### Fixes

- **Gemini `include_server_side_tool_invocations`** (`3b6ddaa`): function calling + Google Search grounding 혼용 시 필수 파라미터 누락 수정.
- **kubectl timeout** (`be0fc6d`): `kubectl wait`에 명시적 timeout 추가.
- **Context consistency** (`e0412fe`): 재시도 루프에서 컨텍스트 일관성 개선.

### Refactoring / Internals

- `harness/llm/artifacts.py`: `write_files()` developer/runtime_verifier 공용화 (`_shared_write_files`).
- `context/` 디렉토리 구조 정리: `prompts/`, `docs/`, `base/`, `knowledge/`, `phases/` 분리 (`4d12d1b`).
- Runtime verifier findings(`.md` 자동 생성) 제거 — state/log로 대체.
- `run.py`: `--max-runtime-retries` CLI 인수 추가.

### Test Coverage

- 전체 158 pass, 1 skipped (cluster.yaml integration).
- manifest 관련 테스트 4개 제거 (160 → 156 + 2 신규 = 158).

---

## v1.0.0

**Tag**: `v-1.0.0`
**Commit**: `4cf9145`

초기 GikView 자동화 하네스. Developer → Static Verifier → Runtime Verifier 3-노드 LangGraph 파이프라인.

### Core Architecture

#### 3-Node LangGraph Pipeline
```
developer → static_verifier → (pass) runtime_verifier → END
                             ↘ (fail) developer
```

- **Developer node** (`1c23924`): LLM이 sub_goal 스펙을 읽고 Helm chart / Dockerfile 생성. `edge-server/` 하위 파일 출력, path prefix 검증 후 원자적 쓰기.
- **Static Verifier node** (`714926e`): yamllint, helm lint, helm template + kubeconform, trivy config, gitleaks, helm dry-run(server). 결정적, LLM 없음.
- **Runtime Verifier node** (`6bcc744`): Phase 1 결정적 게이트 + Phase 2 LLM 진단 2단계 구조.

#### Runtime Phase 1 (`9741257`)
순서형 결정적 체크: `helm upgrade --install` → `kubectl wait pods --for=condition=Ready` → smoke test.
하나 fail 시 이후 단계 skip, 즉시 반환.

#### Developer Node Context System (`07fce2e`)
- `context/base/`: 항상 주입 (conventions.md, tech_stack.md)
- `context/prompts/`: 노드별 system prompt
- `context/knowledge/`: 기술별 on-demand 지식
- 기존 파일 감지 → "Existing Files" 섹션으로 LLM에 제공, 최소 변경 유도

#### kagent MCP Integration (`96f5474`)
kagent MCP 서버를 통해 LLM이 클러스터 read-only 조회 가능:
`GetResources`, `GetPodLogs`, `GetEvents`, `DescribeResource`, `GetRelease`, `CheckServiceConnectivity` 등.

#### Multi-LLM Support (`30bf37c`, `91dc810`)
Anthropic Claude + Google Gemini 양쪽 지원. `harness/llm/client.py`에서 provider 추상화.

### Config & Conventions

- **Namespace**: `cluster.yaml`의 `namespace` 필드 (기본 `gikview`). 전 리소스 통일.
- **Release name**: `<service>-dev-v1`
- **Label selector**: `app.kubernetes.io/name=<service>`
- **Smoke test**: `edge-server/tests/<phase>/smoke-test-<sub_goal>.sh` — 존재 시 자동 실행, 없으면 skip.
- **Helm values split**: `values.yaml` (공통) + `values-dev.yaml` + `values-prod.yaml`.

### Deployment Paths (at v1.0.0)

| 경로 | 배포 방식 |
|------|----------|
| `edge-server/helm/<service>/` | `helm upgrade --install` + `kubectl wait` |
| `edge-server/manifests/<service>/` | `kubectl apply` (pod wait 없음) |
| `edge-server/docker/<service>/` | docker build + push (helm install 전 자동 실행) |
| `edge-server/ebpf/<service>/` | artifact_detection 용도만 (빌드는 수동) |

> **Note**: manifests 경로는 v1.1.0에서 제거됨.

### CLI

```bash
python run.py --phase <phase> --sub-goal <id> [options]
  --max-retries N          # developer 재시도 한도 (default 3)
  --skip-interrupt         # CI 자동 진행
```

### Test Coverage

- 전체 160 pass, 1 skipped (cluster.yaml integration).
