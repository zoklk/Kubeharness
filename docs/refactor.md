# Refactor Plan: Kubeharness — 결정적 CLI + 에이전트 wiring 재사용 패키지

이 문서는 Kubeharness 레포 리팩터링 지시문임. 임의 판단으로 설계 변경 금지.
애매한 지점은 §20 에 따라 사람에게 질문.

---

## 1. 작업 개요

- **현재 상태**: LangGraph 기반 자체 harness. LLM·MCP·상태 머신·Tool loop 전부 Python 내부에 구현. 스캐폴딩만 있고 소비자 프로젝트(`gikview` 등)는 `HARNESS_PROJECT_DIR` 환경변수로 외부에서 참조.
- **목표**: **외부 에이전트 CLI(Claude Code, Codex CLI 등)가 루프·LLM·MCP 를 담당**, 본 레포는 **결정적 검증·배포 CLI + 재사용 가능한 에이전트 wiring 템플릿**만 제공.
- **배포 모델**: Kubeharness 는 pip 설치되는 **라이브러리 + CLI 도구 + 템플릿**. 소비자 프로젝트가 `pip install kubeharness` 후 `python -m harness init` 으로 `.claude/`, `AGENTS.md`, `config/harness.yaml` 을 스캐폴드해 자기 프로젝트에 맞게 커스터마이즈해서 사용.
- **도구 중립성**: 핵심 자산(Python CLI, `config/harness.yaml`, `AGENTS.md`, hook 스크립트) 은 특정 에이전트 CLI 에 종속되지 않음. `.claude/` 는 Claude Code 전용 wiring 예시일 뿐, Codex 도 같은 Python CLI + `AGENTS.md` 를 공유.
- **범위**: 이 레포(Kubeharness) 자체. 외부 소비자 프로젝트는 건드리지 않음.

## 2. 핵심 원칙

1. **하드코딩 최소화** — namespace, release naming, chart path, 워크스페이스 루트 디렉토리명, 체크 목록, 타임아웃 등 **모든 프로젝트별 값은 `config/harness.yaml` 한 곳에만** 정의. Python 코드, hook, slash command, skill 모두 이 설정을 읽음. 코드·템플릿에 `"gikview"`, `"edge-server"`, `"prometheus-dev"` 같은 리터럴이 나타나면 안 됨. 템플릿 파일의 워크스페이스 경로는 `{{workspace_dir}}` (init 치환) 또는 `{workspace}` (런타임 치환) 플레이스홀더로 둠.
2. **파이프라인 단계 기준 분류** — 파일을 tool 타입(kubectl/helm/shell)이 아닌 **언제 실행되는가**로 묶음. 배포 전 정적 검사는 `static.py`, 배포 실행 + 배포 후 검증은 `runtime.py`. 유지보수자가 "배포 후 새 체크 추가" 같은 요구를 받았을 때 **열어볼 파일이 즉시 결정**되도록.
3. **도구 중립성** — Python·config·`AGENTS.md`·hook 스크립트는 어떤 에이전트 CLI 에서도 동작해야 함. Claude Code 전용 기능에 의존 금지. `.claude/` 는 wiring 일 뿐 핵심 아님.
4. **최소주의** — 빈 파일·추상 레이어·선행 최적화 금지. `__init__.py` 제외 빈 스캐폴딩 만들지 않음. 필요해지는 순간에 추가.
5. **LLM 분리** — Python 코드에서 LLM API 를 직접 호출하지 않음. LLM 판단은 모두 에이전트 CLI 본체 또는 subagent 담당.
6. **결정적 CLI 의 출력 제어** — CLI 는 stdout 에 **JSON 요약 + 실패 항목의 log_tail** 만 뱉음. 전체 로그는 **세션 로그 파일 하나**로 저장하고 경로만 알림. 에이전트 컨텍스트 오염 방지.
7. **Skills 는 참조 문서** — 기존 `developer_prompt.md`, `runtime_verifier_prompt.md` 는 `AGENTS.md` 에 녹이지 말고 **작업 단위별 SKILL.md** 로 쪼개서, 해당 작업을 하는 주체(메인 세션·subagent)가 필요할 때만 로드하도록 함 (progressive disclosure).
8. **한 번의 `/deploy` = 한 개의 세션 로그** — 정적 검사, 배포, 동적 검증, 진단, 재시도까지 한 사이클의 모든 출력은 **단일 세션 로그 파일**에 순차 기록. 도구별 개별 로그는 만들지 않음. 관측성 단순화.

## 3. 역할 재분배

기존 LangGraph "노드" 개념 전면 폐기. 역할은 다음과 같이 재할당:

| 기존 노드 | 신규 주체 | 방식 |
|----------|----------|------|
| `developer` (LLM) | **메인 Claude Code 세션** | skills(`helm-chart-author`, `phase-spec-reader` 등) 참조해 Write/Edit 로 직접 코드 작성 |
| `static_verifier` (결정적) | **`python -m harness verify-static`** | 순수 CLI. LLM 없음 |
| `runtime_verifier` Phase 1 (결정적) | **`python -m harness apply` + `verify-runtime`** | 순수 CLI. LLM 없음 |
| `runtime_verifier` Phase 2 (LLM 진단) | **`runtime-diagnoser` subagent** | Read + WebSearch + kagent MCP (read-only). Write/Edit/Bash 없음 |
| (신규 — 기존 Python 루프 역할) | **`deploy-orchestrator` subagent** | Bash(`python -m harness:*`) + Write/Edit(`<workspace_dir>/**`) + Task(runtime-diagnoser). 배포 사이클 자동화 |

"LLM 이 생각하는 지점" 3곳: 메인 세션, deploy-orchestrator, runtime-diagnoser.

`developer` 의 초기 코드 작성 역할은 **메인 세션**이 담당. 배포 사이클의 파일 수정은 **deploy-orchestrator** 가 **사람 승인 후** 일괄 적용 (B' 모델 — §12.2).

## 4. 디렉토리 구조

### 4.1 Kubeharness 레포 (이 레포)

```
Kubeharness/
├── AGENTS.md                     # 이 레포 자체 개발용 (dogfooding)
├── CLAUDE.md                     # 이 레포 개발 강제 규칙
│
├── harness/                      # pip 설치되는 코어 패키지
│   ├── __init__.py
│   ├── __main__.py               # `python -m harness` 엔트리
│   ├── cli.py                    # argparse: init, verify-static, apply, verify-runtime
│   ├── config.py                 # harness.yaml 로드 + resolve(service)
│   ├── shell.py                  # 단일 subprocess runner (세션 로그 append)
│   ├── static.py                 # 배포 전 정적 검증 (감지 기반)
│   ├── runtime.py                # 배포 실행 + 배포 후 검증 (감지 기반)
│   └── init.py                   # `harness init` — 템플릿 복사 + 변수 치환
│
├── templates/                    # 소비자 프로젝트로 복사될 보일러플레이트
│   ├── AGENTS.md.tmpl            # {{project_name}}, {{workspace_dir}} 치환
│   ├── CLAUDE.md.tmpl            # {{project_name}}, {{workspace_dir}} 치환
│   ├── config/
│   │   └── harness.yaml.example.tmpl   # {{workspace_dir}} 치환
│   ├── context/
│   │   ├── conventions.md
│   │   ├── tech_stack.md
│   │   └── phases/
│   │       └── _template.md
│   └── .claude/
│       ├── settings.json.tmpl    # {{workspace_dir}} 치환
│       ├── commands/
│       │   └── deploy.md
│       ├── agents/
│       │   ├── deploy-orchestrator.md
│       │   └── runtime-diagnoser.md
│       ├── hooks/
│       │   ├── guard-path.sh     # config/harness.yaml 의 workspace_dir 를 런타임에 읽음
│       │
│       └── skills/
│           ├── helm-chart-author/SKILL.md
│           ├── docker-author/SKILL.md
│           ├── phase-spec-reader/SKILL.md
│           ├── cluster-env-inject/SKILL.md
│           └── runtime-diagnosis/SKILL.md
│
├── docs/
│   └── design.md                 # 기존 harness_internals.md 이식·갱신
│
├── tests/
│   ├── test_cli.py
│   ├── test_config.py
│   ├── test_static.py
│   └── test_runtime.py
│
├── pyproject.toml
├── README.md
└── .gitignore
```

### 4.2 소비자 프로젝트 (`harness init` 실행 후)

```
/path/to/gikview/                 # 에이전트 CWD
├── AGENTS.md
├── CLAUDE.md
├── .claude/
│   ├── settings.json
│   ├── commands/deploy.md
│   ├── agents/{deploy-orchestrator,runtime-diagnoser}.md
│   ├── hooks/
│   └── skills/
├── config/
│   └── harness.yaml              # 프로젝트별 설정
├── context/
│   ├── conventions.md
│   ├── tech_stack.md
│   ├── knowledge/                # 기술별 참조 지식 (선택)
│   └── phases/
│       └── <phase>.md            # service 선언 (요구사항 명세)
├── <workspace_dir>/              # 기본명 "workspace". harness init --workspace <name> 로 변경 가능.
│   ├── helm/<service>/           # write_allowed_globs 기본 스코프
│   ├── docker/<service>/
│   └── tests/<phase>/smoke-test-<service>.sh
└── logs/
    └── deploy/
        └── <YYYYMMDD-HHMMSS>-<service>.log    # 세션 로그 (한 /deploy = 한 파일)
```

소비자는 Kubeharness 코드를 수정하지 않음. pip 로 업데이트 받고, 자기 프로젝트의 `.claude/`, `AGENTS.md`, `config/harness.yaml` 만 관리.

## 5. 삭제 대상

| 경로 | 이유 |
|---|---|
| `harness/graph.py` | LangGraph 불필요 |
| `harness/state.py` | State 불필요 |
| `harness/nodes/` 전체 | 노드 개념 폐기 |
| `harness/llm/` 전체 | LLM 호출은 에이전트 CLI 담당 |
| `harness/mcp/` 전체 | MCP 연결은 `.claude/settings.json` 의 `mcpServers` 선언 |
| `harness/verifiers/` 디렉토리 | `harness/static.py`, `runtime.py` 로 흡수 |
| `harness/tools/` 디렉토리 | `harness/shell.py` 단일 파일로 통합 |
| `config/llm.yaml` | 불필요 |
| `config/tools.yaml` (외부 CLI 버전 매니페스트 — kubectl/helm/kubeconform/trivy/gitleaks/yamllint 버전만 기록) | 폐기. 설치 안내는 `README.md` 의 prerequisites 섹션에 흡수 |
| `scripts/install_tools.sh` | 폐기. README prerequisites 섹션에 흡수 (CLI 설치는 소비자 책임) |
| `scripts/run.py` | `harness/cli.py` 로 교체 |
| `scripts/` 디렉토리 | 비게 되면 삭제 |
| `tests/test_llm_client.py` | LLM client 삭제 |
| `tests/test_kagent_client.py` | MCP client 삭제 |
| `context/prompts/developer_prompt.md` | `skills/{helm-chart-author, docker-author, phase-spec-reader, cluster-env-inject}/SKILL.md` 로 분해 이식 |
| `context/prompts/runtime_verifier_prompt.md` | `skills/runtime-diagnosis/SKILL.md` + `agents/runtime-diagnoser.md` 로 이관 |
| `context/docs/overview.md` (있다면) | 불필요 |
| `check_gemini_model.py` | LLM 프로바이더 테스트용 |
| `.env` | LLM API 키용. 삭제. `.gitignore` 유지 |
| `gikview_harness.egg-info/` | 구 패키지명 빌드 산출물 |
| 루트의 `log` 파일 (153KB) | 기존 run.py legacy 출력. 세션 로그로 대체 |

## 6. 유지·변환 대상

| 기존 | 신규 위치 | 처리 |
|---|---|---|
| `harness/verifiers/static.py` | `harness/static.py` | 체크 함수 전부 유지. 하드코딩 → `config.resolve(service)` 참조 |
| `harness/verifiers/runtime_gates.py` | `harness/runtime.py` | `helm upgrade --install` + `kubectl wait` 2단계 + smoke test + docker build+push 전부 흡수 |
| `harness/tools/{kubectl,helm,shell}.py` | `harness/shell.py` 하나 | 단일 `run()` 함수 |
| `harness/config.py` | 동일 | 스키마 갱신 (§9) |
| `context/docs/harness_internals.md` | `docs/design.md` | 이동·새 구조 반영해 갱신 |
| `context/base/conventions.md` (**소비자 프로젝트**: `gikview/edge/context/base/conventions.md`) | `templates/context/conventions.md` | **소비자 레포에서 가져와** 템플릿화. 프로젝트 고유 문구 (edge-server/, 네임스페이스 등) 를 일반화 |
| `context/base/tech_stack.md` (소비자 측) | `templates/context/tech_stack.md` | 동일 절차. 템플릿은 "프로젝트별로 채워 넣을 빈칸" 수준 |
| `context/phases/_template.md` (소비자 측) | `templates/context/phases/_template.md` | 소비자의 phase 템플릿 이식. **`**artifacts**:` 필드 추가** (helm/docker 선언). 기존 규칙 블록(A~C 절) 은 그대로 보존, 단 `edge-server/` 리터럴은 `<workspace_dir>/` 로 일반화 |
| `pyproject.toml` | 동일 | 패키지명 `kubeharness`. `langgraph`, `langchain-*`, `anthropic`, `openai`, `google-genai`, `mcp` 제거. `pydantic`, `pyyaml` 유지. dev: `pytest`, `ruff`, `mypy` |
| `README.md` | 동일 | 새 구조 한 페이지 요약 + quickstart |

## 7. 기존 기능 보존 목록

refactor 원칙 (가능하면 기존 기능 포함) 에 따라 아래 기능은 반드시 이식:

| 기존 기능 | 신규 위치 |
|---|---|
| Helm 정적 체크 (yamllint, helm lint, helm template\|kubeconform, trivy config, gitleaks, helm dry-run server) | `harness/static.py` |
| Docker 정적 체크 (hadolint, gitleaks) | `harness/static.py` (docker 경로 감지 시만) |
| `{PREFIX}` 경로 가드 (write guard, `_ALLOWED_WRITE_SUBDIRS`) | `config/harness.yaml` 의 `workspace_dir` + `write_allowed_globs` + `.claude/hooks/guard-path.sh`. `{PREFIX}` 개념은 `workspace_dir` 로 일반화 |
| `{PREFIX}tests/` 쓰기 금지 (smoke test 보호) | `write_denied_globs` + `.claude/settings.json` `deny` |
| `helm uninstall` → `helm upgrade --install` (immutable 충돌 회피) | `harness/runtime.py` |
| Docker build + push (registry) | `harness/runtime.py` |
| `kubectl wait` 2단계 (60s 대기 → terminal 감지 → 실패면 조기 종료, 아니면 240s 추가. CRD-only chart 자동 skip) | `harness/runtime.py` |
| `values.yaml + values-{active}.yaml` 자동 선택 | `harness/config.py` |
| Smoke test 실행 (`<workspace_dir>/tests/<phase>/smoke-test-<service>.sh`) | `harness/runtime.py` |
| Phase 문서 fuzzy heading 매칭 + service spec 추출 | `templates/.claude/skills/phase-spec-reader/SKILL.md` |
| service spec 의 `**technology**:`, `**dependency**:`, `**artifacts**:` 파싱 (헤딩 `## Service: <name>` 이 곧 service 이름) | `phase-spec-reader` skill |
| env 별 `domain_suffix`, `arch`, `nodeSelector` 주입 | `cluster-env-inject` skill + `config/harness.yaml` 의 `environments` 섹션 |
| `failure_source = "smoke_test"` 시 자동 재시도 중단 (사람 개입 요구) | `runtime-diagnoser` 응답 스키마 + `deploy-orchestrator` 정책 |
| `max_runtime_retries` 초과 시 사이클 탈출 | `deploy-orchestrator` 시스템 프롬프트 + `config/harness.yaml` 의 `orchestration.max_runtime_retries` |
| 실패 시 구조화된 JSON 피드백 (관측·제안·files) | `runtime-diagnoser` 응답 스키마 |
| Phase 2 의 proposed_files 자동 적용 후 재배포 (자가 수렴) | **사람 승인(ask) 후** `deploy-orchestrator` 가 일괄 적용 → 풀 파이프라인 재실행 (§12.2) |
| Web search (기술 공식 문서 조회) | `runtime-diagnosis` skill 에 **WebSearch 사용 지침**. Claude Code 내장 `WebSearch` 도구 사용 |
| Technology knowledge 파일 조회 (`context/knowledge/<tech>.md`) | `runtime-diagnosis` skill 에 "소비자 레포 `context/knowledge/` 우선 참조, 없으면 WebSearch" 지시 |

## 8. 기존 기능 제거 목록

refactor 원칙 (불필요한 부분 제거) 에 따라 아래는 폐기:

- 멀티 프로바이더 LLM (`anthropic/gemini/openai_compat` 스위치) → Claude Code/Codex 에 위임
- Anthropic `prompt_caching` 구현 → Claude Code 내장 캐싱에 위임
- `run_tool_loop()`, `request_json_response()` (JSON 재요청 루프) → 에이전트 CLI 자체 tool loop
- `max_tool_turns`, `tool_result_max_chars` 프로파일 키 → CLI 응답이 JSON 요약형이라 불필요. tail 길이는 `config/harness.yaml` 의 `logging.tail_chars` 로 일원화
- `config/llm.yaml` 프로파일 시스템 전체
- LangGraph `interrupt_before` / `interrupt_after` → Claude Code `ask` permission 이 승인 제어를 담당 (슬래시 커맨드는 진입점일 뿐, interrupt 메커니즘 아님)
- `--skip-interrupt` 옵션 → 의미 상실. 사람 승인은 `ask` permission 으로 일원화, 우회 옵션 없음 (승인 스킵이 필요하면 운영자가 settings.json 에서 ask → allow 로 임시 변경)
- `ARTIFACT_PREFIX` 상수 → `config/harness.yaml` 의 `workspace_dir` + `chart_path`/`docker_path` 패턴으로 일반화 (기본 `workspace`, 프로젝트별로 자유 변경)
- `HARNESS_PROJECT_DIR` 환경변수 → 소비자 프로젝트가 각자 `pip install` + CWD 에서 실행 (모델 A)
- `GikView/`(HARNESS_ROOT) vs `gikview/`(PROJECT_ROOT) 루트 분리 → Kubeharness 는 pip 전역 패키지, 소비자 CWD 가 프로젝트 루트
- `build_cluster_env_section()` Python 헬퍼 → `cluster-env-inject` skill + `config/harness.yaml` 의 `environments` 매트릭스
- **도구별 개별 로그 파일** (`logs/<timestamp>-<tool>.log` 등) → 세션 로그 단일 파일로 통합 (§11)
- **슬래시 커맨드 `/verify`, `/diagnose`** → `/deploy` 하나로 축소. 정적 체크만 필요하면 메인 세션이 `python -m harness verify-static` 직접 호출. 관찰만 필요하면 자연어로 diagnoser 호출 (AGENTS.md 워크플로)

## 9. `config/harness.yaml` 스키마 (소비자 프로젝트용)

`harness.yaml.example.tmpl` 은 예시 값을 담되, **필수 재정의 대상**을 주석으로 표시함. 아래 값은 모두 소비자 프로젝트에서 덮어써야 함.

```yaml
cluster:
  # TODO(init): 프로젝트의 k8s namespace 로 변경. 예: "gikview", "observability"
  namespace: default
  # kubeconfig 는 $KUBECONFIG 환경변수가 기본. 명시 필요 시:
  # kubeconfig: ~/.kube/config

conventions:
  # 프로젝트 워크스페이스 루트 디렉토리명. `harness init --workspace <name>` 으로 치환됨.
  # 기본 "workspace". 프로젝트가 이미 쓰는 이름(예: "edge-server", "src", "apps")으로 바꿀 수 있음.
  # 아래 *_path 의 `{workspace}` 는 런타임에 이 값으로 치환됨.
  workspace_dir: workspace

  # artifact 경로 (파일 존재 시에만 단계 실행 — 감지 기반)
  chart_path: "{workspace}/helm/{service}"
  docker_path: "{workspace}/docker/{service}"
  smoke_test_path: "{workspace}/tests/{phase}/smoke-test-{service}.sh"

  # 이름·레이블
  release_name: "{service}"
  label_selector: "app.kubernetes.io/name={service}"

  # values 파일 (순서대로 -f 로 넘겨짐)
  values_files: ["values.yaml", "values-{active_env}.yaml"]

  # 쓰기 허용/차단 경로 (guard-path.sh + harness/static.py 가 공유)
  # `{workspace}` 는 런타임 치환. settings.json 의 glob 은 init 시점에 이미 치환됨 (§10.5).
  write_allowed_globs:
    - "{workspace}/helm/**"
    - "{workspace}/docker/**"
  write_denied_globs:
    - "{workspace}/tests/**"        # smoke test 보호

  # Docker 레지스트리 (build + push). TODO(init): 프로젝트 레지스트리로 변경.
  registry: "registry.example.local/myproject"

environments:
  active: dev                      # verify-runtime / apply 에서 사용할 env
  # TODO(init): 아래 값은 예시. 프로젝트 클러스터에 맞게 수정.
  dev:
    domain_suffix: dev.example.local
    arch: amd64
    node_selectors:
      storage: node-a
      monitoring: node-b
  prod:
    domain_suffix: cluster.local
    arch: arm64
    node_selectors:
      storage: node-p1
      monitoring: node-p2

checks:
  static:
    yamllint: { enabled: true }
    helm_lint: { enabled: true }
    kubeconform: { enabled: true }
    trivy_config: { enabled: true }
    gitleaks: { enabled: true }
    helm_dry_run_server: { enabled: true }
    hadolint: { enabled: true }    # docker 경로 감지 시만 실행
  runtime:
    docker_build_push: { enabled: true }
    helm_upgrade: { enabled: true }
    kubectl_wait:
      enabled: true
      initial_wait_seconds: 60
      terminal_grace_seconds: 240
    smoke_test: { enabled: true }

logging:
  dir: "logs/deploy"               # 세션 로그 루트
  tail_chars: 2000                 # CLI 응답 JSON 의 log_tail 길이
  retention_days: 30               # 세션 로그 보관 기간

orchestration:
  max_runtime_retries: 3           # deploy-orchestrator 의 runtime 실패 자가 수렴 상한
```

`harness/config.py` API:

```python
cfg = load_config()                              # ./config/harness.yaml 을 CWD 기준으로 로드
cfg.cluster.namespace                            # 소비자 설정 값 (예: "myns")
cfg.conventions.workspace_dir                    # "workspace" (또는 프로젝트가 지정한 값)
cfg.resolve("prometheus").release_name           # "prometheus" (release_name 패턴 적용)
cfg.resolve("prometheus").chart_path             # Path("workspace/helm/prometheus") — {workspace} 치환됨
cfg.resolve("prometheus").docker_path            # Path("workspace/docker/prometheus")
cfg.resolve("prometheus").values_files()         # [Path("values.yaml"), Path("values-dev.yaml")]
cfg.active_env                                   # "dev"
cfg.env("dev").domain_suffix                     # 소비자 설정 값
cfg.env("dev").node_selectors["storage"]         # 소비자 설정 값
cfg.checks.static.enabled_names                  # ["yamllint", "helm_lint", ...]
cfg.checks.runtime.kubectl_wait.initial_wait_seconds  # 60
```

`{workspace}` 는 `cfg.conventions.workspace_dir` 로 치환 (다른 `{service}`, `{phase}`, `{active_env}` 는 호출 시점에 바인딩).

`load_config()` 는 프로세스 내부에서 `@lru_cache(maxsize=1)` 로 결과 캐시. 한 CLI 호출 내에 YAML 을 여러 번 파싱하지 않음. 테스트에서 config 파일을 교체해야 하면 `load_config.cache_clear()` 사용.

## 10. 모듈 설계

### 10.1 `harness/shell.py`

```python
@dataclass
class RunResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str

def run(cmd: list[str], *,
        cwd: Path | None = None,
        timeout: int | None = None,
        label: str | None = None) -> RunResult:
    """외부 명령 실행.
    환경변수 HARNESS_SESSION_LOG 가 설정돼 있으면 stdout/stderr 를 해당 파일에 섹션 헤더와 함께 append.
    도구별 개별 로그 파일은 만들지 않음.
    """
```

세션 로그 append 포맷:
```
--- [<label>] $ <command> ---
<stdout>
<stderr>
[exit <code>] (duration: <sec>s)
```

모든 외부 명령은 이 함수를 거침. `subprocess.run` 다른 곳에서 직접 호출 금지.

### 10.2 `harness/static.py`

- 시그니처:
  ```python
  def check_xxx(service: str, cfg: Config) -> CheckResult: ...
  ```
- `CheckResult`: `{name, status: "pass"|"fail"|"skip", detail, log_tail}`
- 공개 API: `run_static(service: str, cfg: Config) -> list[CheckResult]`
- **감지 기반 분기**:
  - `cfg.resolve(service).chart_path.exists()` → helm 체크 그룹 실행
  - `cfg.resolve(service).docker_path.exists()` → docker 체크 그룹 실행
  - 둘 다 없으면 `artifact_detection: fail`
- 체크 등록: 단순 dict 레지스트리
- **LLM 호출 없음**. `shell.run` 으로 외부 CLI 만.
- namespace·release·path 값은 `cfg.resolve(service)` 에서만 얻음.

### 10.3 `harness/runtime.py`

- 공개 API:
  ```python
  def apply(service: str, cfg: Config) -> list[CheckResult]: ...
  def verify_runtime(service: str, cfg: Config) -> list[CheckResult]: ...
  ```
- `apply()` 흐름 (감지 기반):
  1. `docker_path.exists()` → `docker build` + `docker push`
  2. `chart_path.exists()` →
     - 기존 release 있으면 `helm uninstall`
     - `helm upgrade --install` (`--wait` 없음)
- `verify_runtime()` 흐름:
  1. `chart_path.exists()` →
     - `kubectl wait` 2단계: `initial_wait_seconds` 대기 → pod 상태 조회 → terminal(`CrashLoopBackOff`, `ImagePullBackOff`, `ErrImagePull`, `Error`, `OOMKilled`) 감지 시 즉시 fail, 아니면 `terminal_grace_seconds` 추가 대기
     - `helm template` 분석 후 workload 리소스가 없는 CRD-only chart 는 자동 skip
  2. `smoke_test_path` 존재 → 실행 (kubectl wait 실패 시 skip)
  3. `chart_path` 없고 `docker_path` 만 존재 → push 성공 여부가 runtime 통과

### 10.4 `harness/cli.py`

서브커맨드 4개:

```bash
python -m harness init [--dest .] [--force] [--name <project-name>]
python -m harness verify-static --service <name>
python -m harness apply --service <name>
python -m harness verify-runtime --service <name>
```

- 환경변수 `HARNESS_SESSION_LOG` 를 감지해 `shell.run` 에 전파 (별도 인자 없음)
- stdout: JSON (§11 형식)
- stderr: 필요 시 Rich 요약 (선택적)
- exit code: `0` pass, `1` check fail, `2` config/env error
- **파이프라인 순서 강제(`/deploy`) 는 CLI 가 아니라 `.claude/commands/deploy.md` + `deploy-orchestrator` 에이전트에서** (원칙 3 + 4)

### 10.5 `harness/init.py`

`python -m harness init [--dest .] [--force] [--name <project>] [--workspace <dir>]`:
- `templates/` 를 `--dest` (기본 `.`) 로 복사. 이미 존재하는 파일은 `--force` 없으면 skip + 경고
- `.tmpl` 확장자 파일은 단순 `{{var}}` 치환 후 확장자 제거:
  - `{{project_name}}`: `--name` 또는 `basename(dest)`
  - `{{workspace_dir}}`: `--workspace` 또는 기본값 `workspace`. `settings.json.tmpl`, `harness.yaml.example.tmpl`, `AGENTS.md.tmpl`, `CLAUDE.md.tmpl` 등에서 치환됨
  - `{{kubeharness_version}}`: 현재 설치된 패키지 버전
- `--workspace` 는 프로젝트가 이미 `edge-server/`, `src/`, `apps/` 같은 디렉토리를 쓰고 있을 때 지정. 미지정 시 `workspace` 가 생성되고, `config/harness.yaml` 과 `.claude/settings.json` 의 glob 이 같은 값으로 고정됨
- 복사 후 안내 메시지:
  - "`config/harness.yaml` 을 프로젝트에 맞게 수정할 것 (특히 `cluster.namespace`, `conventions.registry`, `environments.*`)"
  - "`<workspace_dir>/` 디렉토리에 helm/docker/tests 하위를 구현할 것"
  - "`AGENTS.md` 의 프로젝트별 규칙 섹션을 보완할 것"
- 최소 의존성: stdlib 만

## 11. CLI 응답 형식 + 세션 로그 (원칙 6, 8)

### 11.1 CLI stdout JSON

```json
{
  "service": "prometheus",
  "stage": "verify-static",
  "summary": "6 passed, 1 failed, 0 skipped",
  "passed": false,
  "session_log": "logs/deploy/20260417-074500-prometheus.log",
  "checks": [
    {
      "name": "yamllint",
      "status": "pass",
      "detail": null,
      "log_tail": null
    },
    {
      "name": "helm_lint",
      "status": "fail",
      "detail": "templates/deployment.yaml:15: invalid indent",
      "log_tail": "... 끝 2000자 (config.logging.tail_chars) ..."
    }
  ]
}
```

- `session_log`: 최상위에 세션 로그 경로 한 번만 포함 (모든 check 가 같은 파일에 기록됨)
- `log_tail`: 실패 항목에만 채움. 전체 출력은 `session_log` 파일 참조
- 도구별 log_path 없음 (통합 로그만)

### 11.2 세션 로그

**경로**: `logs/deploy/<YYYYMMDD-HHMMSS>-<service>.log` (루트는 `config/harness.yaml` 의 `logging.dir` 기준)

**한 번의 `/deploy` = 한 파일**. 정적 검사, 배포, 동적 검증, 진단, 재시도까지 모든 출력이 시간순으로 append.

**구조 예시**:
```
=== deploy prometheus @ 20260417-074500 ===
retry: 0/3

--- [verify-static] $ yamllint workspace/helm/prometheus ---
(output)
[exit 0] (0.3s)

--- [verify-static] $ helm lint workspace/helm/prometheus -f ... ---
(output)
==> ERROR: templates/deployment.yaml:15 invalid indent
[exit 1] (0.5s)

[verify-static] summary: 6 passed, 1 failed
[orchestrator] verify-static 실패. 메인 세션에 보고 후 종료.

=== done (failed: static) @ 20260417-074523 ===
```

runtime 실패 후 진단·재시도가 있는 경우:
```
--- [verify-runtime] $ kubectl wait ... ---
(output)
[exit 1] (305s)

[verify-runtime] summary: 2 passed, 1 failed (kubectl_wait)

[diagnoser] called
failure_source: implementation
observations:
  - pod prometheus-0 CrashLoopBackOff: missing config key scrape_timeout
proposed_files:
  - workspace/helm/prometheus/values-dev.yaml (1 modification)

[user approval] requested for 1 file modification
[user approval] granted @ 20260417-074812

[orchestrator] applied 1 file(s)

=== retry 1/3 @ 20260417-074815 ===

--- [verify-static] ... ---
...
```

**메커니즘**:
- `deploy-orchestrator` 는 세션 시작 시 `SESSION_ID` 를 계산해 **LLM 메모리에 보관**하고, 이후 모든 Bash 호출마다 **inline 환경변수 주입**:
  ```bash
  HARNESS_SESSION_LOG=logs/deploy/20260417-074500-prometheus.log \
    python -m harness verify-static --service prometheus
  ```
  Claude Code `Bash` 도구는 호출마다 새 shell 을 스폰하므로 `export` 는 호출 간 지속되지 않음. 매 호출마다 같은 경로 문자열을 prefix 해 주입해야 함.
- `shell.run` 은 이 환경변수를 읽어 append. 미설정이면 자동 기본 경로 생성 (§11.2 standalone 규칙).
- orchestrator 자체 이벤트(진단 호출, 사람 승인, 파일 적용, retry 카운터)는 orchestrator 가 **같은 경로에 직접 append**:
  ```bash
  printf '%s\n' "[orchestrator] applied 1 file(s)" >> logs/deploy/20260417-074500-prometheus.log
  ```

**사람이 CLI 직접 호출 시** (`python -m harness verify-static --service X`):
- `HARNESS_SESSION_LOG` 미설정 → CLI 가 자동으로 기본 경로 생성 (`logs/deploy/<ts>-<service>-standalone.log`)
- JSON 응답의 `session_log` 필드로 경로 반환

**보관**: `config.logging.retention_days` (기본 30일) 초과 세션 로그는 다음 `/deploy` 시 orchestrator 가 정리 (optional).

## 12. Subagents

### 12.1 `runtime-diagnoser`

- 위치: `templates/.claude/agents/runtime-diagnoser.md`
- 도구 허용:
  ```yaml
  tools:
    - Read
    - WebSearch
    # kagent MCP — Claude Code 는 서버 tool 을 `mcp__<server>__<tool>` 로 surface.
    # <tool> 은 kagent 서버 노출 이름(snake_case)임 — 소비자 `config/kagent.yaml` 참고.
    - mcp__kagent__k8s_get_pod_logs
    - mcp__kagent__k8s_describe_resource
    - mcp__kagent__k8s_get_events
    - mcp__kagent__k8s_get_resources
    - mcp__kagent__k8s_get_resource_yaml
    - mcp__kagent__k8s_check_service_connectivity
    - mcp__kagent__k8s_get_available_api_resources
    - mcp__kagent__k8s_get_cluster_configuration
    - mcp__kagent__helm_get_release
    - mcp__kagent__helm_list_releases
  ```
  **Write/Edit/Bash 없음** (진단 전용). 도구 이름은 kagent 서버 버전에 따라 변할 수 있으므로 소비자가 `.claude/agents/runtime-diagnoser.md` 를 설치 클러스터에 맞게 갱신.
- 입력: `deploy-orchestrator` 가 Task prompt 문자열에 직렬화해 넘김. 최소 필수 필드: `service`, `failed_stage`, `session_log`, 마지막 stage 의 CLI 응답 JSON (`checks` 배열 포함). 선택 필드: `service_spec` (phase-spec-reader 가 뽑은 요구사항)
- 시스템 프롬프트: `runtime-diagnosis` skill 본문 + 출력 스키마 명세
- 출력 JSON:
  ```json
  {
    "service": "prometheus",
    "failure_source": "implementation" | "smoke_test" | "environment",
    "observations": ["pod CrashLoopBackOff — config key missing", "..."],
    "proposed_files": [
      {"path": "workspace/helm/prometheus/values-dev.yaml",
       "content": "..."}
    ],
    "suggestions": ["retention 을 30d 로 조정", "..."]
  }
  ```

### 12.2 `deploy-orchestrator` (B' — 사람 승인 기반)

- 위치: `templates/.claude/agents/deploy-orchestrator.md`
- 도구 허용:
  ```yaml
  tools:
    - Bash             # python -m harness:* 전용 (settings.json permissions 에서 제한)
    - Read
    - Write
    - Edit
    - Task             # runtime-diagnoser 호출
  ```
  **kubectl / helm / docker 직접 호출 권한 없음.** 모든 배포 동작은 `python -m harness:*` 내부 로직이 담당.

- 시스템 프롬프트 (사이클 규칙):

  **세션 초기화**
  1. `SESSION_ID=$(date +%Y%m%d-%H%M%S)` 생성
  2. `export HARNESS_SESSION_LOG="logs/deploy/${SESSION_ID}-${SERVICE}.log"`
  3. 세션 로그에 헤더 쓰기: `=== deploy $SERVICE @ $SESSION_ID ===`

  **파이프라인 (retry_count = 0 부터 시작)**
  1. `python -m harness verify-static --service $SERVICE`
     - fail → 세션 로그에 기록 후 **즉시 중단**, 메인 세션에 요약 보고 (정적 오류는 초기 코드 작성 주체가 수정)
  2. `python -m harness apply --service $SERVICE`
     - fail → 중단, 보고
  3. `python -m harness verify-runtime --service $SERVICE`
     - pass → 성공 반환
     - fail → §step-diagnose 로

  **step-diagnose (runtime 실패 시)**
  1. `Task` 도구로 `runtime-diagnoser` 호출. Claude Code `Task` 는 prompt 문자열만 받으므로, 입력을 마크다운/JSON 블록으로 직렬화해 prompt 본문에 담음:
     ```
     Task(subagent_type="runtime-diagnoser", prompt="""
     service: prometheus
     failed_stage: verify-runtime
     session_log: logs/deploy/<...>.log
     ```json
     { "checks": [ ...verify-runtime CLI JSON 응답... ] }
     ```
     """)
     ```
     diagnoser 는 session_log 경로를 Read 로 조회해 상세 로그를 확인.
  2. 응답 파싱:
     - `failure_source == "smoke_test"` → 즉시 중단, suggestions 를 메인에 보고 (사람이 smoke test 검토)
     - `proposed_files` 비고 `suggestions` 만 존재 → 중단, suggestions 보고 (사람 개입)
     - `proposed_files` 존재 → §step-approve 로
  3. `retry_count >= cfg.orchestration.max_runtime_retries` 확인 → 초과면 중단, 보고

  **step-approve (사람 승인 + 일괄 적용)**
  1. 메인 세션에 diagnoser 결과 요약 + `proposed_files` 의 diff 전달
  2. 각 파일 수정 시 Write/Edit 도구가 `ask` permission 에 걸려 자동으로 사람 승인 요구됨
  3. 승인 거부 → 중단, "사람이 수정을 거절함" 으로 보고
  4. 승인 → `proposed_files` 를 **한 번에 일괄 적용** (모두 성공해야 다음 단계)
  5. 세션 로그에 `[orchestrator] applied N file(s)` 기록
  6. `retry_count += 1` → **파이프라인 1번부터 재실행** (수정된 파일이 static 체크를 다시 통과해야 하므로 풀 파이프라인)

  **최종 반환 (메인 세션)**
  ```json
  {
    "service": "prometheus",
    "passed": true|false,
    "retries": 2,
    "session_log": "logs/deploy/20260417-074500-prometheus.log",
    "last_stage": "verify-runtime",
    "summary": "..."
  }
  ```

- **static 실패는 orchestrator 가 재시도하지 않음**. 초기 코드 문법 오류는 메인 세션 책임.
- **runtime 실패만 자가 수렴 사이클**. 환경·의존성·값 오류가 전형적.
- **사람 승인은 `ask` permission 에 위임**. orchestrator 가 직접 승인 UI 를 만들지 않고, `Write/Edit(<workspace_dir>/**)` 가 `ask` 리스트에 등록돼 있어 자동으로 승인 프롬프트가 뜸. workspace_dir 값은 `harness init` 시점에 settings.json 에 치환됨.

## 13. Skills (원칙 7)

모든 skill 은 `templates/.claude/skills/<name>/SKILL.md`. 프론트매터:

```markdown
---
name: <slug>
description: <한 줄. 언제 이 skill 을 로드해야 하는지>
---

<본문 — 참조 문서 성격>
```

| Skill | description 요지 | 본문 내용 |
|---|---|---|
| `helm-chart-author` | Helm chart 파일(Chart.yaml, values*, templates/\*)을 작성하거나 수정할 때 | 디렉토리 구조, Chart.yaml 필드, values/values-dev/values-prod 분리 규칙, 릴리스 이름 패턴, 레이블 컨벤션, `config/harness.yaml` 참조 방법 |
| `docker-author` | Dockerfile 을 작성하거나 수정할 때 | 멀티스테이지 빌드 패턴, 보안 베이스 이미지 선택, hadolint 규칙 회피 요령, registry 푸시 준비 |
| `phase-spec-reader` | `context/phases/<phase>.md` 에서 특정 service 사양을 추출할 때 | `## Service: <name>` 헤딩 fuzzy 매칭, `**technology**:`, `**dependency**:`, `**artifacts**:` 필드 의미, service 섹션 경계 결정 로직 |
| `cluster-env-inject` | `values-dev.yaml` / `values-prod.yaml` 에 env 별 값을 채울 때 | `config/harness.yaml` 의 `environments.dev/prod` 매트릭스 읽는 법, nodeSelector / domain_suffix / arch 주입 예시 |
| `runtime-diagnosis` | 배포 실패 원인을 진단할 때 (주로 `runtime-diagnoser` subagent 가 참조) | kagent 도구 사용 패턴, 로그·이벤트·describe 조회 순서, `failure_source` 분류 기준, **WebSearch 로 공식 문서 조회 지침**, 소비자 `context/knowledge/<tech>.md` 우선 참조 규칙 |

- skill 본문은 **참조 문서**. 에이전트가 작업 시작 전 Read 로 로드 → 끝나면 잊음 (progressive disclosure).
- `AGENTS.md` 에는 "어떤 상황에 어떤 skill 을 참조하라" 1–2줄 색인. 본문 복제 금지.

## 14. Slash Command

`templates/.claude/commands/deploy.md` **하나만**.

### `/deploy <service>`

- 실행 주체: 메인 세션이 **`deploy-orchestrator` subagent 호출** (Task)
- 동작: §12.2 의 사이클 규칙
- 사람 승인: `.claude/settings.json` permissions 의 `ask` 리스트에 `Write(<workspace_dir>/**)`, `Edit(<workspace_dir>/**)` 포함 (init 시 구체 값 치환) → orchestrator 가 파일 적용 시 자동 승인 프롬프트

**다른 작업**:
- **정적 체크만 필요**: 메인 세션이 `!python -m harness verify-static --service X` 직접 호출
- **관찰만 필요**: 사람이 자연어로 "prometheus 왜 이상해?" → 메인 세션이 `Task(runtime-diagnoser)` 호출 (AGENTS.md 에 명시)
- **스캐폴드**: 사람이 자연어로 "phase X 의 Y service 구현해줘" → 메인 세션이 phase-spec-reader + helm-chart-author 등 skill 참조해서 작성

## 15. 워크플로

### 15.1 신규 service 구현 (scratch)

```
1. 사람: "phase observability 의 prometheus service 구현해줘"
2. 메인 세션:
   - phase-spec-reader skill 로드 → observability.md 에서 prometheus 섹션 추출
   - **artifacts**: 확인 (예: "helm, docker")
   - helm-chart-author / cluster-env-inject / docker-author skill 로드
   - <workspace_dir>/helm/prometheus/{Chart.yaml, values*.yaml, templates/*} 작성
   - <workspace_dir>/docker/prometheus/Dockerfile 작성
   - 사람에게 "작성 완료. /deploy prometheus 실행할까요?" 확인
3. 사람: /deploy prometheus
4. deploy-orchestrator 가 §12.2 사이클 수행
5. 성공 → 메인에 요약 + 세션 로그 경로 보고
   smoke_test 실패 → 사람이 smoke test 검토 후 수동 재시도
   max_retries 초과 → 사람이 상황 파악 후 개입
```

### 15.2 기존 서비스 수정 (iterate)

```
1. 사람: "prometheus retention 을 30d 로 늘려줘"
2. 메인 세션: helm-chart-author skill 참조해 values.yaml 수정
3. 사람: /deploy prometheus
4. 이하 동일
```

### 15.3 관찰만 필요할 때

```
1. 사람: "prometheus 왜 이상해?"
2. 메인 세션: AGENTS.md 워크플로 따라 Task(runtime-diagnoser) 호출
3. diagnoser: kagent 로 현재 상태 조사 → observations/suggestions 반환
4. 메인 세션이 사람에게 보고. 수정·재배포는 사람 판단 후 /deploy 로.
```

## 16. `.claude/settings.json` 골격 (템플릿)

템플릿 파일명: `templates/.claude/settings.json.tmpl` (init 시점에 `{{workspace_dir}}` 치환 후 `.tmpl` 제거)

```json
{
  "permissions": {
    "allow": [
      "Bash(python -m harness:*)",
      "Read(**)"
    ],
    "deny": [
      "Write(harness/**)",
      "Write(.claude/**)",
      "Write(context/**)",
      "Write(config/**)",
      "Write(docs/**)",
      "Write({{workspace_dir}}/tests/**)",
      "Bash(kubectl delete:*)",
      "Bash(helm uninstall:*)"
    ],
    "ask": [
      "Write({{workspace_dir}}/**)",
      "Edit({{workspace_dir}}/**)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {"matcher": "Write|Edit",
       "hooks": [{"type": "command", "command": "bash .claude/hooks/guard-path.sh"}]}
    ],
    "PostToolUse": []
  },
  "mcpServers": {
    "kagent": {
      "// TODO(init)": "소비자 클러스터의 kagent 서비스 DNS + 포트로 교체",
      "url": "http://kagent-tools.kagent:8084/mcp",
      "transport": "streamable_http",
      "allowedTools": [
        "k8s_get_resources", "k8s_get_resource_yaml", "k8s_describe_resource",
        "k8s_get_events", "k8s_get_pod_logs", "k8s_check_service_connectivity",
        "k8s_get_available_api_resources", "k8s_get_cluster_configuration",
        "helm_get_release", "helm_list_releases"
      ]
    }
  }
}
```

주의:
- `Write/Edit({{workspace_dir}}/**)` 가 `ask` 에 걸려 있어 메인 세션·orchestrator 의 파일 수정 시 자동 사람 승인 프롬프트 발생 → B' 모델의 승인 메커니즘. init 시점에 구체 디렉토리명으로 치환됨 (예: `Write(workspace/**)`, `Write(edge-server/**)`).
- hook 스크립트는 `config/harness.yaml` 의 `workspace_dir`/`write_allowed_globs`/`write_denied_globs`/`checks.static.enabled_names` 를 읽어 동작. 하드코딩 금지.
- `runtime-diagnoser` / `deploy-orchestrator` subagent 의 tools 제한은 각 agent 파일의 frontmatter 에 정의 → 상위 settings 의 권한과 무관하게 격리.

## 17. 구현 순서

**1단계: 코어 (클러스터 없이 가능)**
1. 삭제 목록(§5) 적용. 빈 디렉토리·legacy `log` 파일 정리.
2. `pyproject.toml` 정비 (패키지명 `kubeharness`, 의존성 축소).
3. `harness/config.py`: 새 스키마 파싱 + `resolve(service)` + `active_env` API.
4. `harness/shell.py`: `run()` + 세션 로그 append 로직.
5. `harness/static.py`: 기존 체크 함수 이식·포팅. 감지 기반 분기.
6. `harness/runtime.py`: `apply()` + `verify_runtime()`. docker/helm 감지 기반 + `kubectl wait` 2단계 이식.
7. `harness/cli.py`: 서브커맨드 4개 + §11 JSON 응답 형식 + `HARNESS_SESSION_LOG` 전파.
8. `harness/init.py`: 템플릿 복사 + `{{var}}` 치환.
9. `tests/`: `shell.run` 을 mock 한 단위 테스트.

**2단계: 템플릿 작성**
10. `templates/config/harness.yaml.example.tmpl`: §9 스키마 (TODO(init) 주석 포함).
11. `templates/AGENTS.md.tmpl`, `CLAUDE.md.tmpl`: 프로젝트별 규칙 골격 + skill 인덱스 + "관찰 시 diagnoser 호출" 워크플로.
12. `templates/.claude/settings.json.tmpl`: §16.
13. `templates/.claude/hooks/guard-path.sh`: config 기반 동작.
14. `templates/.claude/commands/deploy.md`: §14.
15. `templates/.claude/agents/{deploy-orchestrator,runtime-diagnoser}.md`: §12 (세션 로그 관리 규칙 포함).
16. `templates/.claude/skills/*/SKILL.md`: 기존 프롬프트 분해 이식.
17. `templates/context/`: conventions, tech_stack, phases/_template.md (`**artifacts**:` 필드 추가).

**3단계: 문서**
18. `docs/design.md`: 기존 `harness_internals.md` 이식·갱신.
19. `README.md`: 한 페이지 요약 (설치 + `harness init` + `/deploy` 예시).

**4단계: 자체 dogfooding (선택)**
20. 이 레포 자체에 `python -m harness init` 실행 → Kubeharness 개발에도 `/deploy` 적용 (helm 배포 대상 없으면 verify-static 만 유의미).

각 단계 완료 후 다음으로. 1단계는 클러스터 없이 전부 가능.

## 18. 금기사항

- `langgraph`, `langchain-*`, `anthropic`, `openai`, `google-genai`, `mcp` 패키지 재도입
- `harness/` 내부에서 LLM API 직접 호출
- tool 타입별 파일(`kubectl.py`, `helm.py`) 재생성
- 검증 단계별 노드 파일(`developer_node.py`, `static_verifier_node.py`) 재생성
- `__init__.py` / `__main__.py` 외 빈 스캐폴딩 파일
- namespace, release name, chart path, docker path 를 `config/harness.yaml` 외부에 리터럴로 둠
- `deploy-orchestrator` subagent 에 `Bash(kubectl:*)`, `Bash(helm:*)`, `Bash(docker:*)` 또는 kagent MCP 권한 부여 — 모든 배포 동작은 `python -m harness:*` 를 거쳐야 함
- `runtime-diagnoser` subagent 에 `Write`/`Edit`/`Bash` 권한 부여 (읽기 전용)
- Python 코드가 subagent 직접 호출 시도 (Task 는 에이전트 CLI 전용)
- `.claude/settings.json` 의 `deny` 없이 workspace 루트 밖 쓰기 허용
- hook 스크립트·템플릿·코드 안에서 `"edge-server"`, `"gikview"` 등 워크스페이스·프로젝트명 리터럴 하드코딩 — `config/harness.yaml` 의 `workspace_dir` 또는 init 치환(`{{workspace_dir}}`)으로 일반화
- CLI stdout 에 전체 로그 덤프 (log_tail 만, 전체는 세션 로그 파일)
- **도구별 개별 로그 파일 생성** — 한 `/deploy` = 하나의 세션 로그만
- 슬래시 커맨드 2개 이상 신설 (`/deploy` 외엔 AGENTS.md 워크플로로)
- `AGENTS.md` / `CLAUDE.md` 에 skill 본문 복제 (index 참조만)

## 19. 완료 체크리스트

- [ ] `harness/` 하위에 `graph.py`, `state.py`, `nodes/`, `llm/`, `mcp/`, `verifiers/`, `tools/` 없음
- [ ] `harness/` 내 파이썬 모듈은 `cli.py`, `config.py`, `shell.py`, `static.py`, `runtime.py`, `init.py` 6개 + `__init__.py`, `__main__.py`
- [ ] Kubeharness 레포 자체엔 `config/` 디렉토리 없음. 템플릿은 `templates/config/` 에만
- [ ] `templates/` 하위에 `.claude/`, `AGENTS.md.tmpl`, `CLAUDE.md.tmpl`, `config/harness.yaml.example.tmpl`, `context/` 모두 존재
- [ ] `templates/.claude/commands/` 에 `deploy.md` 하나만 (verify.md, diagnose.md, ship.md 없음)
- [ ] `templates/.claude/agents/` 에 `deploy-orchestrator.md`, `runtime-diagnoser.md` 두 개
- [ ] `templates/.claude/skills/` 에 `helm-chart-author`, `docker-author`, `phase-spec-reader`, `cluster-env-inject`, `runtime-diagnosis` 5개 SKILL.md
- [ ] 모든 namespace/release/path/workspace 리터럴이 소비자 프로젝트 `config/harness.yaml` 또는 `templates/config/harness.yaml.example.tmpl` 에만 존재. 템플릿 내부는 `{{workspace_dir}}` 또는 `{workspace}` 플레이스홀더
- [ ] `subprocess.run` 호출이 `harness/shell.py` 내부에만 존재
- [ ] `templates/.claude/settings.json.tmpl` 의 `deny` + `PreToolUse` hook 조합으로 `{{workspace_dir}}/**` 외부 + `{{workspace_dir}}/tests/**` 쓰기 차단
- [ ] `Write/Edit({{workspace_dir}}/**)` 가 `ask` 에 등록돼 B' 승인 모델 작동 (init 시 구체 디렉토리명으로 치환)
- [ ] `/deploy <service>` 실행 시 verify-static → apply → verify-runtime → (실패 시 diagnose + 사람 승인 + 일괄 적용 + 풀 파이프라인 재시도) 순서가 `deploy-orchestrator` 시스템 프롬프트에서 강제됨
- [ ] `runtime-diagnoser` subagent 의 tools 목록에 Write/Edit/Bash 없음
- [ ] `deploy-orchestrator` 의 Bash 권한이 `python -m harness:*` 로 제한 (kubectl/helm/docker 직접 호출 불가)
- [ ] `pyproject.toml` 에 `langgraph`, `langchain-*`, `anthropic`, `openai`, `google-genai`, `mcp` 의존성 없음
- [ ] 빈 스캐폴딩 파일 없음 (`__init__.py`, `__main__.py` 제외)
- [ ] `tests/` 가 삭제 모듈을 참조하지 않음
- [ ] `python -m harness init` 이 빈 디렉토리에서 정상 동작 (템플릿 복사 + `{{project_name}}` / `{{workspace_dir}}` 치환)
- [ ] `python -m harness init --workspace edge-server` 로 워크스페이스 디렉토리명 변경 시 `config/harness.yaml` 과 `.claude/settings.json` 이 동일한 값으로 고정됨
- [ ] CLI 응답이 모두 §11.1 JSON 형식 (전체 로그 stdout 덤프 없음, `session_log` 경로만 포함)
- [ ] **세션 로그 하나만** — 도구별 개별 로그 파일 없음. `shell.run` 이 `HARNESS_SESSION_LOG` 환경변수 존재 시 append, 없으면 자동 기본 경로 생성
- [ ] `kubectl wait` 2단계 로직(`initial_wait_seconds` + terminal 감지 + `terminal_grace_seconds`) 이식
- [ ] `helm uninstall` → `helm upgrade --install` 패턴 이식
- [ ] `context/phases/_template.md` 에 `**artifacts**:` 필드 추가
- [ ] 루트의 legacy `log` 파일 삭제, 디렉토리는 `logs/deploy/` 로 이전

## 20. 애매한 지점은 질문

설계 상 모호한 경우 임의 결정 금지. 특히 다음은 사람에게 질문:

- `config/harness.yaml` 스키마에 필드 추가가 필요해 보일 때
- 새 체크 함수의 stage(static/runtime) 분류가 애매할 때
- `CLAUDE.md` 와 `AGENTS.md` 에 포함할 강제 규칙의 경계
- hook 이 체크 실패 시 Claude Code 에 돌려줄 피드백 형식
- skill 본문에 기존 프롬프트의 어느 부분을 이식할지 (중복·축약 판단)
- `deploy-orchestrator` 사이클의 종료 조건이 spec 에서 커버 안 되는 케이스
- phase.md 의 `**artifacts**:` 필드 표기 방식 (리스트 vs 인라인 vs 별도 섹션)
- `harness init` 실행 시 기존 파일 충돌 처리 정책 (덮어쓰기 / 머지 / 스킵)
- 사람이 승인 거부 시(한 번만 거부 vs 전체 취소) orchestrator 의 동작
- 세션 로그 용량 관리 정책 — retention_days 외에 파일당 최대 크기 제한이 필요한지
- Kubeharness 자체 dogfooding 의 실용성 — 배포 대상 없이 verify-static 만 의미 있는가
- **apply 실패 시 diagnose 호출 여부** — 현재 설계는 runtime 실패에만 diagnose. docker build 실패 (Dockerfile 오류, base image 404 등) 도 diagnoser 가 도울 수 있지만, 메인 세션이 직접 수정하게 하는 현 정책 유지할지
- **verify-runtime `--env <name>` CLI 오버라이드** — 현재는 `environments.active` 만 사용. ad-hoc 으로 prod 검증이 필요한 경우 대응 방법
- **release_name 패턴 단순화 결정 (2026-04)** — `{service}-{active_env}-v1` → `{service}` 로 축소. dev/prod 가 다른 kubeconfig·네임스페이스로 격리돼 한 클러스터에서 충돌할 일이 없고, `-v1` 은 Helm revision 과 중복이라 제거. phase 스펙의 `## Service: <name>` 헤딩이 곧 릴리스 이름.

## 21. 구현자 참조 노트 (문서 자기완결성 보조)

이 섹션은 refactor.md 만 읽고 구현할 수 있게 기존 코드에서 반드시 확인·이식할 항목을 명시함. **본 설계를 바꾸지 않고 이식만** 함.

### 21.1 정적 체크 함수 목록 (이식 대상)

기존 `harness/verifiers/static.py` 의 체크 함수를 `harness/static.py` 로 이식. 각 체크는 `(service, cfg) → CheckResult`.

Helm 그룹 (chart_path 존재 시):
- `yamllint`: `yamllint -c <rules> <chart_path>`
- `helm_lint`: `helm lint <chart_path> -f values.yaml -f values-<env>.yaml`
- `helm_template_kubeconform`: `helm template ... | kubeconform -strict -summary`
- `trivy_config`: `trivy config <chart_path>`
- `gitleaks`: `gitleaks detect --source <chart_path> --no-git`
- `helm_dry_run_server`: `helm upgrade --install --dry-run=server <release> <chart_path> -n <namespace> -f ...`

Docker 그룹 (docker_path 존재 시):
- `hadolint`: `hadolint <docker_path>/Dockerfile`
- `gitleaks_docker`: `gitleaks detect --source <docker_path> --no-git`

각 함수는 stdout/stderr 를 `shell.run` 에 위임 — 세션 로그 자동 append. 실패 시 `detail` 에 사람이 읽을 한 줄 요약, `log_tail` 에 `config.logging.tail_chars` 만큼.

### 21.2 Docker 워크플로 세부

`apply()` 의 Docker 단계 (runtime.py):

1. **이미지 태그**: `<registry>/<service>:<tag>`. 태그는 **`release_name` 에서 파생 + git short sha 또는 timestamp** 중 택1. 구현자는 기존 `runtime_gates.py` 의 태그 규칙을 그대로 이식 (본 문서가 강제하는 규칙 아님 — 단 `config/harness.yaml` 의 `conventions.image_tag` (신규 선택 필드) 로 오버라이드 가능하게 열어두는 것을 권장).
2. **build**: `docker build --platform linux/<env.arch> -t <image>:<tag> <docker_path>`. `env.arch` 는 `cfg.env(cfg.active_env).arch` 에서 가져옴 (dev 면 amd64, prod 면 arm64 등).
3. **push**: `docker push <image>:<tag>`.
4. **실패 시**: build/push 실패면 `apply()` 는 즉시 중단 — helm 단계로 넘어가지 않음.
5. **이미지 태그를 helm 에 넘기기**: 빌드 성공 후 helm 의 `--set image.tag=<tag>` 로 주입하거나, `values-<env>.yaml` 이 이미 태그를 명시하는 경우 그대로 사용. 구현자는 기존 코드의 방식(둘 중 무엇을 썼는지)을 유지.
6. **`verify_runtime` 에서 docker-only 서비스**: `chart_path` 없고 `docker_path` 만 존재 → `apply()` 의 push 성공이 runtime 통과 기준. smoke test 는 실행 불가(클러스터에 없는 이미지라) → skip.

Docker 정적 체크는 §21.1 hadolint + gitleaks 로 분리돼 배포 전에 실행. build 는 runtime 단계에서만 (비용 큼).

### 21.3 `kubectl wait` 2단계 로직 상세

```
terminal_states = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
                   "Error", "OOMKilled"}

1. sleep initial_wait_seconds (기본 60s)
2. kubectl get pods -n <ns> -l <label_selector> -o json
   - 모든 pod container 의 waiting.reason / terminated.reason 확인
   - terminal_states 에 하나라도 해당 → 즉시 fail (terminal_grace 대기 skip)
3. kubectl wait -n <ns> -l <label_selector> --for=condition=Ready \
        --timeout=<terminal_grace_seconds>s  (기본 240s)
   - 성공 → pass
   - 타임아웃 → fail
```

CRD-only chart skip:
- `helm template <chart>` 실행 후 출력 YAML 의 `kind` 필드 수집
- workload kind 가 `{Deployment, StatefulSet, DaemonSet, Job, CronJob, Pod, ReplicaSet}` 중 하나라도 있으면 대기
- 전부 CRD/RBAC/ConfigMap/Secret 류면 kubectl wait skip → pass

### 21.4 Smoke test 실행 규약

- 경로: `<workspace_dir>/tests/<phase>/smoke-test-<service>.sh`
- 실행: `bash <path>` — cwd 는 프로젝트 루트 (소비자 CWD)
- 환경변수 주입:
  - `SERVICE`, `NAMESPACE`, `RELEASE_NAME`, `ACTIVE_ENV`, `DOMAIN_SUFFIX`
- exit 0 → pass, 그 외 → fail (`detail` 에 마지막 출력 라인)
- 스크립트 없으면 skip (runtime 통과)
- **스크립트는 사람이 작성**. orchestrator / diagnoser 는 수정 금지 (settings.json deny)

### 21.5 helm upgrade 명령 상세

- **`helm uninstall` 선행 조건**: `helm status <release> -n <ns>` 가 존재하는 release 를 리턴할 때만. immutable field 충돌 (StatefulSet 의 volumeClaimTemplates 등) 회피 목적.
- **upgrade 명령**:
  ```
  helm upgrade --install <release> <chart_path> \
    -n <namespace> --create-namespace \
    -f values.yaml -f values-<active_env>.yaml \
    --timeout 60s
  ```
  `--wait` 는 쓰지 않음 (verify_runtime 단계의 `kubectl wait` 2단계가 대신).
- **실패 시**: helm 자체 exit ≠ 0 이면 apply fail. verify_runtime 으로 넘어가지 않음.

### 21.6 values 파일 탐색

`cfg.resolve(service).values_files()` 반환 원칙:
- `values.yaml` 필수. 없으면 `artifact_detection: fail`.
- `values-<active_env>.yaml` 선택. 있으면 포함, 없으면 경고 로그만 남기고 values.yaml 만 사용.
- 둘 다 `chart_path` 기준 상대 경로.

### 21.7 Phase 문서 포맷 (phase-spec-reader skill 의 파서 입력)

`context/phases/<phase>.md` 의 service 섹션 예시:

```markdown
## Service: prometheus

**technology**: kube-prometheus-stack v58
**artifacts**: helm, docker
**dependency**: [none]

<요구사항 본문 — 사람이 자유 서술>
```

- 헤딩 규칙: `## Service: <name>` 의 `<name>` 이 곧 service 이름 (Helm 릴리스, 차트 디렉터리, label 값). 별도 `**service_name**:` 필드 없음.
- fuzzy heading 매칭: `## Service:` 접두사 뒤의 이름을 대소문자·`_`/`-`/공백 동등으로 비교
- 섹션 경계: 다음 `##` heading 또는 EOF
- `**artifacts**:` 값은 `helm`, `docker` 중 하나 이상 (쉼표 구분). 이 필드가 메인 세션에 "어떤 디렉토리를 스캐폴드할지" 를 알림
- 이 파서는 skill 본문의 규칙 명세만 제공. 코드에는 파서 없음 (메인 LLM 이 판단).

### 21.8 Hook 스크립트 계약 (guard-path.sh)

**guard-path.sh** (PreToolUse, matcher=Write|Edit):
- stdin: Claude Code 가 넘기는 JSON `{tool_name, tool_input: {file_path, ...}}`
- 로직: `file_path` 가 `config/harness.yaml` 의 `write_allowed_globs` 매칭되는지 확인. `write_denied_globs` 는 차단. workspace_dir 밖은 차단.
- 차단 시 exit 2 + stderr 에 한 줄 이유 → Claude Code 가 블록하고 사유를 LLM 에 전달.

`config/harness.yaml` 은 `yq` 또는 inline python 으로 파싱 — 리터럴 금지.

### 21.9 CLI exit code 분류

- `0`: 모든 체크 pass
- `1`: 체크 실패 (정적이든 런타임이든). JSON 의 `passed: false`
- `2`: config 파싱 실패, 필수 CLI 도구 부재, stage 선행 조건 실패 (예: verify-runtime 인데 apply 안 됨)

orchestrator 는 exit 0/1 만 의미 있게 해석, 2 면 메인 세션에 구성 오류로 즉시 중단 보고.

### 21.10 이식 시 주의 — 기존 코드에서 "버리지 말 것"

기존 `harness/verifiers/runtime_gates.py` 의 다음 세부는 문서로 재서술하기 까다로워서 **코드 레벨에서 보존**:

- `helm template` 출력을 YAML stream 으로 파싱해 workload 리소스 유무 판정하는 로직
- `kubectl get pods -o json` 의 `status.containerStatuses[*].state.{waiting,terminated}.reason` 추출 로직
- `helm status` 결과의 exit code 해석 (release 없음 vs 명령 실패 구분)
- docker build 실패 시 stderr 의 마지막 에러 라인을 `detail` 로 올리는 규칙

이식 시 위 로직은 **그대로 복사**, 단 namespace/release/path 리터럴만 `cfg.resolve(service)` 참조로 바꿈.
