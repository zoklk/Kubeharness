# Kubeharness

Kubernetes 위에서 Helm · Docker 배포를 굴리기 위한 **결정적 CLI + 에이전트 CLI 연동 템플릿** 패키지. 파이썬 패키지는 재현 가능한 부수 효과(lint, build, push, upgrade, wait, smoke)를 소유하고, 판단과 루프는 외부 에이전트 CLI(Claude Code, Codex CLI 등)가 담당합니다.

설계 배경은 `docs/design.md` 참고.

## 설치

```bash
git clone <이 레포 URL> kubeharness
cd kubeharness
pip install -e .
```

### 사전 준비

CLI 는 아래 도구들을 shell out 해서 사용합니다. 없는 도구는 해당 체크를 **`skip`** 처리하고 fail 로 만들지 않으니, 실제 쓰는 것만 설치하면 됩니다.

| 도구 | 용도 |
|---|---|
| `helm` (v3) | chart lint, template, upgrade, uninstall |
| `kubectl` | 배포 후 wait, pod 조회 |
| `docker` (+ buildx) | 이미지 build · push |
| `yamllint` | chart YAML hygiene (pip 의존성으로 자동 포함) |
| `kubeconform` | k8s 스키마 검증 (`helm template \| kubeconform`) |
| `hadolint` | Dockerfile lint |
| `trivy` | chart config scan (선택) |
| `gitleaks` | 시크릿 스캔 (선택) |

각 도구 설치법은 upstream 문서를 따르세요. Kubeharness 는 설치 스크립트를 동봉하지 않습니다.

#### 설치 위치

위 도구들은 모두 **시스템 `$PATH` 에 있는 실행 파일**이면 됩니다. `pip install` 로는 설치할 수 없습니다 (Go 바이너리). venv 활성화 여부와도 무관 — `which helm`, `which kubectl` 이 어느 디렉터리에서든 같은 결과를 내면 정상입니다. 설치 경로 예시:

- apt/brew/공식 설치 스크립트 사용 시: `/usr/local/bin/`, `/usr/bin/`
- 사용자 로컬 설치: `~/.local/bin/` (PATH 에 포함돼 있는지 확인)
- `asdf`/`mise` 등 버전 매니저: 각 툴 shim 경로

kubeharness 는 호출 시점에 `command not found` 를 감지해 **fail 이 아니라 `skip`** 으로 처리하므로, 당장 쓰지 않는 도구는 설치하지 않아도 됩니다. 실제 검사 결과는 `python -m harness verify-static --service <svc>` 의 JSON 응답에서 각 체크의 `status` 로 확인.

#### kagent (선택)

`kagent` 는 로컬 바이너리가 **아니라** 클러스터 안에서 도는 MCP 서비스입니다. Claude Code 가 MCP 프로토콜로 붙어서 `mcp__kagent__k8s_*` · `mcp__kagent__helm_*` 툴을 `runtime-diagnoser` 서브에이전트의 런타임 실패 진단에 사용합니다 (주로 리소스 조회, 필요 시 pod 내부 `exec` 로 DNS·연결 확인).

kubeharness 의 파이썬 CLI 자체는 kagent 에 의존하지 **않습니다** — kagent 없이도 `verify-static` / `apply` / `verify-runtime` 은 모두 동작합니다. kagent 는 **에이전트 루프의 진단 품질을 올리는 확장**이고, 아래 두 곳에 이미 wiring 만 해 둔 상태입니다:

1. **MCP 서버 URL** — `.claude/settings.json` 의 `mcpServers.kagent.url`. init 직후 `TODO(init): replace with kagent service DNS + port ...` 플레이스홀더 문자열이 들어가 있으니, 본인 클러스터의 kagent 서비스 DNS+포트 (예: `http://kagent-tools.kagent:<port>/mcp`) 로 교체하세요.
2. **툴 권한** — `.claude/settings.json` 에 kagent 툴이 두 단계로 나뉘어 있습니다. 조회성 툴(`k8s_get_*`, `k8s_describe_*`, `helm_get_*`, `cilium_*` 등)은 `permissions.allow` 에 있어 자동 승인, pod 내부에서 임의 명령을 실행하는 `mcp__kagent__k8s_execute_command` 만 `permissions.ask` 에 있어 호출마다 사람 승인을 받습니다. 조회도 프롬프트를 받고 싶으면 해당 항목을 `allow` 에서 `ask` 로 옮기고, kagent 를 아예 쓰지 않으려면 이 라인들을 삭제하세요.

kagent 설치 자체는 upstream 문서 참조. 쓰지 않기로 했다면 `settings.json` 의 `mcpServers.kagent` 블록을 통째로 지우세요 — 없는 MCP 서버를 참조해도 치명적이진 않지만 세션 시작 시 연결 실패 경고가 뜹니다.

## 빠른 시작

```bash
# 1. 소비자 프로젝트 스캐폴드 (빈 디렉터리 또는 기존 repo 에서).
mkdir my-infra && cd my-infra
python -m harness init --name my-infra --workspace workspace

# 2. config/harness.yaml 편집 — TODO(init) 주석이 붙은 값은
#    첫 배포 전에 반드시 교체 (cluster.namespace, registry, environments.*).
$EDITOR config/harness.yaml

# 3. 배포 단위(sub_goal) 명세 작성.
cp context/phases/_template.md context/phases/observability.md
$EDITOR context/phases/observability.md   # service_name, artifacts: helm, docker, …

# 4. 아티팩트 작성. 두 가지 방법:
#    (a) 에이전트 세션에서 메인 Claude 에게 요청.
#        phase-spec-reader 로 sub_goal 을 읽고,
#        helm-chart-author · docker-author 스킬을 따라
#        workspace/helm/<svc>/, workspace/docker/<svc>/ 를 채웁니다.
#    (b) 또는 수동으로 파일 직접 작성.
mkdir -p workspace/helm/prometheus workspace/docker/prometheus
# ... Chart.yaml, values.yaml, Dockerfile 작성 ...

# 5. 아티팩트가 갖춰진 뒤 배포 파이프라인 실행.
#    (a) 에이전트 CLI 에 위임 — Claude Code 세션에서:
#            /deploy observability prometheus
#        → deploy-orchestrator 가 context/phases/observability.md 에서
#           sub_goal "prometheus" 를 찾아 service_name 을 뽑고,
#           verify-static → apply → verify-runtime 을 재시도 버짓과
#           함께 돌립니다. (아티팩트 생성은 하지 않음)
#    (b) 또는 CLI 를 직접 호출:
python -m harness verify-static  --service prometheus
python -m harness apply           --service prometheus
python -m harness verify-runtime  --service prometheus \
    --phase observability --sub-goal prometheus
```

각 명령은 JSON 한 덩어리를 stdout 으로 내고 다음 중 하나의 exit code 로 종료합니다:

- `0` 모두 통과
- `1` 하나 이상 fail
- `2` 설정 오류 (YAML 누락 · 스키마 위반 등)

전체 subprocess 출력은 `logs/deploy/<ts>-<service>-<stage>-standalone.log` 에 남습니다(호출 전 `$HARNESS_SESSION_LOG` 를 export 했다면 그 경로).

## init 직후 프로젝트 구조

위 1단계의 `python -m harness init --name my-infra --workspace workspace` 직후 만들어지는 트리:

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
└── .claude/                    # Claude Code 전용 wiring (다른 에이전트는 무시)
    ├── settings.json           # 권한(allow/deny/ask) + PreToolUse/PostToolUse 훅 + kagent MCP
    ├── commands/deploy.md      # /deploy <phase> <sub_goal> 슬래시 명령
    ├── agents/
    │   ├── deploy-orchestrator.md   # 메인 배포 루프 (Write/Edit 가능)
    │   └── runtime-diagnoser.md     # 런타임 실패 진단 전담 (read-only)
    ├── skills/                 # 5개: helm-chart-author · docker-author ·
    │                           #      phase-spec-reader · cluster-env-inject ·
    │                           #      runtime-diagnosis
    └── hooks/
        ├── guard-path.sh       # workspace 밖 Write/Edit 차단 (PreToolUse)
        ├── verify-on-write.sh  # 편집 직후 yamllint · hadolint 즉시 피드백 (PostToolUse)
        └── log-tool-call.sh    # Write/Edit/Task/kagent 호출을 세션 로그에 기록 (PostToolUse)
```

이후 배포할 수 있는 상태로 만들려면:

1. `cp config/harness.yaml.example config/harness.yaml` 후 `TODO(init)` 마크된 값 교체
   — `cluster.namespace`, `conventions.registry`, `conventions.image_tag`, `environments.*` 네 군데가 기본
2. `context/conventions.md`, `context/tech_stack.md` 를 **본인 프로젝트 맥락으로 덮어 쓰기** — 템플릿 상태로 두면 에이전트가 엉뚱한 전제를 가져감
3. `context/phases/_template.md` 를 복사해 `context/phases/<phase>.md` 작성. 각 sub_goal 의 `**artifacts**:` 목록이 에이전트가 만들 파일 종류를 결정. 코드만 봐선 추론 못 하는 도메인 제약 (클러스터링 디스커버리, 특수 포트, 스토리지 요구사항) 이 있으면 `context/knowledge/_template.md` 를 복사해 `<tech>.md` 로 채우고, sub_goal 의 `**references**:` 에 경로를 넣으세요 — phase-spec-reader 가 artifact 작성 전에 자동 로드합니다
4. 에이전트 루프(4a) 또는 CLI 직접 호출(4b) 로 배포 실행
5. (GitHub 연동 시) `git init` · `.gitignore` 에 `logs/`, `.harness/`, `.claude/settings.local.json` 추가 후 첫 커밋. `.claude/` 본체는 팀 공용이니 커밋 대상 (`.harness/` 는 `session-path` 가 쓰는 런타임 포인터 디렉터리라 gitignore)

`harness/`, `docs/` 은 생기지 **않습니다** — 그건 kubeharness 레포 자체의 파일이고, 소비자 프로젝트는 `pip install` 된 패키지를 `python -m harness` 로 호출만 합니다. 템플릿 원본은 `harness/templates/` 로 패키지에 번들돼 있습니다.

## 에이전트 CLI 워크플로

`init` 은 slash command 1개(`/deploy`), subagent 2개(`deploy-orchestrator`, `runtime-diagnoser`), skill 5개, 그리고 권한/훅 wiring 까지 포함한 `.claude/` 트리를 함께 만들어 줍니다. Claude Code 에서:

```
/deploy <phase> <sub_goal>     # 예: /deploy observability prometheus
```

을 실행하면 orchestrator 가 `context/phases/<phase>.md` 의 해당 sub_goal 섹션에서 `service_name` 을 읽어 내고, verify-static → apply → verify-runtime 을 재시도 버짓과 함께 돌립니다. runtime 실패는 read-only 인 `runtime-diagnoser` 가 진단하고, 제안된 파일 수정은 `{workspace_dir}/**` 범위에서 **중단 없이** 적용됩니다 — PreToolUse `guard-path` 훅이 경로를 검증하고, PostToolUse `log-tool-call` 훅이 모든 Write/Edit/Task/kagent 호출을 세션 로그에 남겨 사후 감사가 가능합니다 (LLM 은 이 로그를 못 읽으니 토큰 영향 없음). 전체 규약은 `init` 이 생성하는 `AGENTS.md` 에 정리돼 있습니다.

Codex CLI 등 다른 에이전트 러너는 같은 `AGENTS.md` 를 읽습니다. `.claude/` 트리는 **Claude Code 전용 wiring 예시**이니, 다른 CLI 를 쓰면 무시하세요.

## 템플릿 업데이트

kubeharness 쪽 skill/agent/hook/명령 문서가 바뀐 뒤 이미 init 해둔 프로젝트에 반영하려면:

```bash
python -m harness update --dry-run     # 덮어쓸 파일 미리보기
python -m harness update                # 실제 덮어쓰기
```

**덮어쓰는 것**: `.claude/agents/`, `.claude/skills/`, `.claude/hooks/`, `.claude/commands/`, `AGENTS.md`, `CLAUDE.md` (하네스 소유 — 사용자가 손댈 이유가 없는 곳).
**건드리지 않는 것**: `config/**`, `context/**`, `{workspace_dir}/**`, `.claude/settings.json` (사용자 영역).

`{{project_name}}` 과 `{{workspace_dir}}` 는 `AGENTS.md` 의 첫 줄과 `config/harness.yaml` 의 `conventions.workspace_dir` 에서 자동 감지합니다. 필요하면 `--name` · `--workspace` 로 명시 가능. `settings.json` 이 바뀐 경우는 이 명령이 처리하지 않으니 `harness/templates/.claude/settings.json.tmpl` 과 수동 diff 하세요.

## 설정

`config/harness.yaml` 이 단일 진실의 원천입니다. 주요 섹션:

- `cluster` — namespace, 선택적 kubeconfig
- `conventions` — 워크스페이스 디렉터리, chart · docker · smoke 경로 패턴, 릴리스 이름, 이미지 태그, 레지스트리, 쓰기 허용/차단 glob
- `environments.<env>` — domain suffix, arch(도커 `--platform` 용), node selectors. `active` 가 `apply` / `verify-runtime` 의 기준 env
- `checks.static.*` / `checks.runtime.*` — enable 플래그, `kubectl_wait` 타이밍
- `logging` — 세션 로그 디렉터리, JSON 응답의 `log_tail` 길이, 보관 기간
- `orchestration` — orchestrator subagent 의 `max_runtime_retries` 버짓

전체 스키마는 인라인 주석과 함께 `harness/templates/config/harness.yaml.example.tmpl`(또는 스캐폴드된 `config/harness.yaml.example`)에 있습니다.

## 레포 구조

```
kubeharness/
├── harness/               # 파이썬 패키지 (6 모듈 + 엔트리포인트)
│   └── templates/         # `harness init` 이 복사할 트리 (wheel 번들)
│       ├── AGENTS.md.tmpl · CLAUDE.md.tmpl
│       ├── config/harness.yaml.example.tmpl
│       ├── .claude/       # Claude Code 전용 wiring
│       └── context/       # conventions.md · tech_stack.md · phases/_template.md
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

테스트는 모든 subprocess 호출을 label 단위로 가로채 미리 정해 둔 결과로 돌려주도록 돼 있습니다. 그래서 클러스터 · docker · 외부 CLI 어느 것도 없이 2초 안에 끝납니다.
