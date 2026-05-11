# Kubeharness

Kubernetes 위에서 Helm · Docker 배포를 굴리기 위한 **결정적 CLI + 에이전트 CLI 연동 템플릿** 패키지. 파이썬 패키지는 재현 가능한 부수 효과(lint, build, push, upgrade, wait, smoke)를 소유하고, 판단과 루프는 외부 에이전트 CLI(Claude Code, Codex CLI 등)가 담당함.

설계 배경은 `docs/design.md` 참고.

## 설치

```bash
git clone <이 레포 URL> kubeharness
cd kubeharness
pip install -e .
```

### 사전 준비

CLI 는 아래 도구들을 shell out 해서 사용함. 없는 도구는 해당 체크를 **`skip`** 처리하므로, 실제 쓰는 것만 설치하면 됨.

| 도구 | 용도 |
|---|---|
| `helm` (v3) | chart lint, template, upgrade, uninstall |
| `kubectl` | 배포 후 wait, pod 조회 |
| `docker` (+ buildx) | 이미지 build · push |
| `yamllint` | chart YAML hygiene (pip 의존성으로 자동 포함) |
| `kubeconform` | k8s 스키마 검증 (`helm template \| kubeconform`) |
| `hadolint` | Dockerfile lint |
| `trivy` | chart config scan (선택) — 출력 요약에 `jq` 필요 |
| `gitleaks` | 시크릿 스캔 (선택) |

각 도구 설치법은 upstream 문서 참조. Kubeharness 는 설치 스크립트를 동봉하지 않음.

#### 설치 위치

위 도구들은 모두 **시스템 `$PATH` 에 있는 실행 파일**이면 됨 (`pip install` 불가, Go 바이너리). 설치 경로 예시:

- apt/brew/공식 설치 스크립트: `/usr/local/bin/`, `/usr/bin/`
- 사용자 로컬: `~/.local/bin/` (PATH 포함 여부 확인)
- `asdf`/`mise` 등 버전 매니저: 각 툴 shim 경로

검사 결과는 `python -m harness verify-static --service <svc>` JSON 응답의 `status` 로 확인.

#### kagent (선택)

`kagent` 는 클러스터 안에서 도는 MCP 서비스임. Claude Code 가 MCP 프로토콜로 붙어 `mcp__kagent__k8s_*` · `mcp__kagent__helm_*` 툴을 `runtime-diagnoser` 의 런타임 실패 진단에 사용함.

파이썬 CLI 자체는 kagent 에 의존하지 **않음** — 없어도 `verify-static` / `apply` / `verify-runtime` 은 모두 동작함. kagent 는 **진단 품질을 올리는 확장**이고, 아래 두 곳에 wiring 만 해 둔 상태임:

1. **MCP 서버 URL** — `.mcp.json` (프로젝트 루트). init 직후 `mcpServers.kagent.url` 에 `TODO(init): replace with kagent service DNS + port ...` 플레이스홀더가 들어가 있으므로, 본인 클러스터의 kagent 서비스 DNS+포트 (예: `http://kagent-tools.kagent:8084/mcp`) 로 교체할 것. Claude Code 는 프로젝트 MCP 서버를 `.mcp.json` 또는 `~/.claude.json` 에서만 읽는다 — `.claude/settings.json` 에 `mcpServers` 를 둬도 무시되니 주의.
2. **툴 권한** — `.claude/settings.json` 에 kagent 툴이 두 단계로 나뉘어 있음. 조회성 툴(`k8s_get_*`, `k8s_describe_*`, `helm_get_*`, `cilium_*` 등)은 `permissions.allow` 로 자동 승인, pod 내부 임의 명령을 실행하는 `mcp__kagent__k8s_execute_command` 만 `permissions.ask` 로 호출마다 승인. 조회도 프롬프트를 받으려면 `allow` → `ask` 이동, 아예 쓰지 않으려면 라인 삭제.
3. **Trust 승인** — `.mcp.json` 이 새로 생기거나 변경된 직후 첫 Claude Code 세션에서 `Use this MCP server?` 다이얼로그가 뜸. enable 해야 `/mcp` 에 `kagent connected` 가 잡힘. 거절했다면 `~/.claude.json` 의 `disabledMcpjsonServers` 에 들어갔다는 뜻 — 거기서 빼고 재시작.

쓰지 않기로 했다면 `.mcp.json` 의 `kagent` 블록을 통째로 지울 것 — 그냥 두면 세션 시작 시 연결 실패 경고가 뜸.

## 빠른 시작

```bash
# 1. 소비자 프로젝트 스캐폴드.
mkdir my-infra && cd my-infra
python -m harness init --name my-infra --workspace workspace

# 2. config/harness.yaml 편집 — TODO(init) 값은 첫 배포 전 반드시 교체
#    (cluster.namespace, registry, environments.*).
$EDITOR config/harness.yaml

# 3. 배포 단위(service) 명세 작성.
cp context/phases/_template.md context/phases/observability.md
$EDITOR context/phases/observability.md   # ## Service: <name>, artifacts: helm, docker, …

# 4. 아티팩트 작성. 두 가지 방법:
#    (a) 에이전트 세션에서 메인 Claude 에게 요청 —
#        phase-spec-reader 로 service 스펙을 읽고
#        helm-chart-author · docker-author 스킬로
#        workspace/helm/<svc>/, workspace/docker/<svc>/ 를 채움.
#    (b) 수동 작성.
mkdir -p workspace/helm/prometheus workspace/docker/prometheus
# ... Chart.yaml, values.yaml, Dockerfile 작성 ...

# 5. 배포 파이프라인 실행.
#    (a) Claude Code 세션에서:
#            /deploy observability prometheus
#        → deploy-orchestrator 가 phase 문서에 선언된 service 를
#           verify-static → apply → verify-runtime 을 재시도 버짓과 함께 돌림.
#    (b) CLI 직접 호출:
python -m harness verify-static  --service prometheus
python -m harness apply           --service prometheus
python -m harness verify-runtime  --service prometheus --phase observability
```

각 명령은 JSON 한 덩어리를 stdout 으로 내고 아래 exit code 로 종료함:

- `0` 모두 통과
- `1` 하나 이상 fail
- `2` 설정 오류 (YAML 누락 · 스키마 위반 등)

전체 subprocess 출력은 `logs/deploy/<ts>-<service>-<stage>-standalone.log` 에 남음 (호출 전 `$HARNESS_SESSION_LOG` 를 export 했다면 그 경로).

## init 직후 프로젝트 구조

```
my-infra/
├── AGENTS.md                   # 에이전트 공통 운영 가이드 (Claude Code · Codex CLI 공용)
├── CLAUDE.md                   # Claude Code 전용 규칙 · 금지 행위
├── config/
│   └── harness.yaml.example    # 복사 후 harness.yaml 로 편집 (TODO(init) 값 채우기)
├── context/
│   ├── conventions.md          # 프로젝트 아키텍처 · 네이밍 규약 (사용자 작성)
│   ├── tech_stack.md           # 주 스택 · 런타임 · 버전 (사용자 작성)
│   ├── phases/_template.md     # 새 phase 문서의 뼈대 — 복사해 <phase>.md 로 사용
│   └── knowledge/_template.md  # 도메인 지식 메모 뼈대 — 복사해 <tech>.md 로 사용
│                               #   (클러스터링/포트/스토리지 등 코드로 추론 불가한 제약)
├── workspace/                  # 사용자 아티팩트 영역 (--workspace 로 이름 지정)
│   └── tests/_template.sh      # 스모크 테스트 뼈대 — <phase>/smoke-test-<service>.sh 로 복사
├── .mcp.json                   # Claude Code 가 읽는 프로젝트 MCP 서버 정의 (kagent URL)
└── .claude/                    # Claude Code 전용 wiring (다른 에이전트는 무시)
    ├── settings.json           # 권한(allow/deny/ask) + PreToolUse/PostToolUse 훅
    ├── commands/deploy.md      # /deploy <phase> <service> 슬래시 명령
    ├── agents/
    │   ├── deploy-orchestrator.md   # 메인 배포 루프 (Write/Edit 가능)
    │   └── runtime-diagnoser.md     # 런타임 실패 진단 전담 (read-only)
    ├── skills/                 # 5개: helm-chart-author · docker-author ·
    │                           #      phase-spec-reader · cluster-env-inject ·
    │                           #      runtime-diagnosis
    └── hooks/
        ├── guard-path.sh       # workspace 밖 Write/Edit 차단 (PreToolUse)
        └── log-tool-call.sh    # Write/Edit/Task/kagent 호출을 세션 로그에 기록 (PostToolUse)
```

배포 가능한 상태로 만들려면:

1. `cp config/harness.yaml.example config/harness.yaml` 후 `TODO(init)` 값 교체 — `cluster.namespace`, `conventions.registry`, `conventions.image_tag`, `environments.*` 네 군데
2. `context/conventions.md`, `context/tech_stack.md` 를 **본인 프로젝트 맥락으로 덮어 쓸 것** — 템플릿 그대로 두면 에이전트가 엉뚱한 전제를 가져감
3. `context/phases/_template.md` 를 복사해 `context/phases/<phase>.md` 작성. 각 service 의 `**artifacts**:` 가 생성할 파일 종류를 결정함. 코드로 추론 못 하는 도메인 제약(클러스터링 디스커버리, 특수 포트, 스토리지 요구사항)은 `context/knowledge/_template.md` 를 복사해 `<tech>.md` 로 채우고 service 의 `**references**:` 에 경로를 넣을 것 — phase-spec-reader 가 artifact 작성 전에 자동 로드함
4. 에이전트 루프(4a) 또는 CLI 직접 호출(4b) 로 배포 실행
5. (GitHub 연동 시) `git init` · `.gitignore` 에 `logs/`, `.harness/`, `.claude/settings.local.json` 추가 후 첫 커밋. `.claude/` 본체는 팀 공용이라 커밋 대상

`harness/`, `docs/` 은 생기지 **않음** — kubeharness 레포 자체의 파일이고, 소비자 프로젝트는 `pip install` 된 패키지를 `python -m harness` 로 호출만 함. 템플릿 원본은 `harness/templates/` 로 패키지에 번들됨.

## 에이전트 CLI 워크플로

`init` 은 slash command 1개(`/deploy`), subagent 2개(`deploy-orchestrator`, `runtime-diagnoser`), skill 5개, 권한/훅 wiring 까지 `.claude/` 트리에 생성함. Claude Code 에서:

```
/deploy <phase> <service>      # 예: /deploy observability prometheus
```

을 실행하면 orchestrator 가 `context/phases/<phase>.md` 에 `## Service: <service>` 섹션이 있는지 확인하고 verify-static → apply → verify-runtime 을 재시도 버짓과 함께 돌림. runtime 실패는 read-only `runtime-diagnoser` 가 진단하고, 제안된 파일 수정은 `{workspace_dir}/**` 범위에서 **중단 없이** 적용됨 — PreToolUse `guard-path` 훅이 경로를 검증, PostToolUse `log-tool-call` 훅이 모든 Write/Edit/Task/kagent 호출을 세션 로그에 기록함. 전체 규약은 `AGENTS.md` 참조.

Codex CLI 등 다른 러너도 같은 `AGENTS.md` 를 읽음. `.claude/` 트리는 **Claude Code 전용 wiring 예시**이므로 다른 CLI 를 쓰면 무시할 것.

## 스모크 테스트

`verify-runtime` 의 마지막 체크. `kubectl wait` 로 pod 가 Ready 된 뒤 실행되며, 실제 기능 (HTTP 요청, port-forward 로 API 호출 등) 을 검증함.

- **경로**: `{workspace}/tests/{phase}/smoke-test-{service}.sh` (`conventions.smoke_test_path`)
- **템플릿**: `{workspace}/tests/_template.sh` — 복사해 채우면 됨
- **주입 env**: `SERVICE`, `NAMESPACE`, `RELEASE_NAME`, `ACTIVE_ENV`, `DOMAIN_SUFFIX`
- **반환값**: `exit 0` → pass, 비0 → fail (stdout+stderr 가 detail 로 기록됨)
- **시간 제한**: 전체 120s 이내
- **비활성화**: `config/harness.yaml` 의 `checks.runtime.smoke_test.enabled = false`

파일이 없는 service 는 detection-gated 로 `skip` 처리됨 — 검증할 게 없으면 안 만들어도 됨.

## 템플릿 업데이트

kubeharness 쪽 skill/agent/hook/명령 문서 변경을 이미 init 한 프로젝트에 반영하려면:

```bash
python -m harness update --dry-run     # 덮어쓸 파일 미리보기
python -m harness update                # 실제 덮어쓰기
```

**덮어쓰는 것**: `.claude/agents/`, `.claude/skills/`, `.claude/hooks/`, `.claude/commands/`, `AGENTS.md`, `CLAUDE.md` (하네스 소유).
**건드리지 않는 것**: `config/**`, `context/**`, `{workspace_dir}/**`, `.claude/settings.json`, `.mcp.json` (사용자 영역).

`{{project_name}}` 과 `{{workspace_dir}}` 는 `AGENTS.md` 첫 줄과 `config/harness.yaml` 의 `conventions.workspace_dir` 에서 자동 감지함. 필요하면 `--name` · `--workspace` 로 명시 가능. `settings.json` · `.mcp.json` 변경은 이 명령이 처리하지 않으므로 `harness/templates/.claude/settings.json.tmpl` · `harness/templates/.mcp.json.tmpl` 과 수동 diff 할 것. 기존 프로젝트에 `.mcp.json` 이 없는 경우(예: 0.1.x → 2.0.0 마이그레이션) 새 init 을 같은 디렉터리에서 다시 돌리면 missing 파일만 생성됨 — `python -m harness init --workspace <기존값>`.

## 설정

`config/harness.yaml` 이 단일 진실의 원천임. 주요 섹션:

- `cluster` — namespace, 선택적 kubeconfig
- `conventions` — 워크스페이스 디렉터리, chart · docker · smoke 경로 패턴, 릴리스 이름, 이미지 태그, 레지스트리, 쓰기 허용/차단 glob
- `environments.<env>` — domain suffix, arch(도커 `--platform` 용), node selectors. `active` 가 `apply` / `verify-runtime` 의 기준 env
- `checks.static.*` / `checks.runtime.*` — enable 플래그, `kubectl_wait` 타이밍
- `logging` — 세션 로그 디렉터리, JSON 응답의 `log_tail` 길이, 보관 기간
- `orchestration` — orchestrator subagent 의 `max_runtime_retries` 버짓

전체 스키마는 `harness/templates/config/harness.yaml.example.tmpl` (또는 스캐폴드된 `config/harness.yaml.example`) 의 인라인 주석 참조.

## 레포 구조

```
kubeharness/
├── harness/               # 파이썬 패키지 (6 모듈 + 엔트리포인트)
│   └── templates/         # `harness init` 이 복사할 트리 (wheel 번들)
│       ├── AGENTS.md.tmpl · CLAUDE.md.tmpl
│       ├── config/harness.yaml.example.tmpl
│       ├── .claude/       # Claude Code 전용 wiring
│       └── context/       # conventions.md · tech_stack.md · phases/_template.md · knowledge/_template.md
├── docs/design.md         # 아키텍처 레퍼런스
├── tests/                 # pytest (클러스터 불필요)
├── MANIFEST.in            # 템플릿 트리 sdist/wheel 포함용
└── pyproject.toml
```

## 개발

```bash
pip install -e .[dev]
.venv/bin/pytest tests/ -v
```

테스트는 subprocess 호출을 label 단위로 가로채 미리 정해 둔 결과로 돌려줌. 클러스터 · docker · 외부 CLI 없이 2초 안에 끝남.
