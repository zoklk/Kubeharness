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
│   └── phases/_template.md     # 새 phase 문서의 뼈대 — 복사해 <phase>.md 로 사용
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
        ├── guard-path.sh       # workspace 밖 Write/Edit 차단
        └── verify-on-write.sh  # 편집 직후 yamllint · hadolint 즉시 피드백
```

이후 배포할 수 있는 상태로 만들려면:

1. `cp config/harness.yaml.example config/harness.yaml` 후 `TODO(init)` 마크된 값 교체
   — `cluster.namespace`, `conventions.registry`, `conventions.image_tag`, `environments.*` 네 군데가 기본
2. `context/conventions.md`, `context/tech_stack.md` 를 **본인 프로젝트 맥락으로 덮어 쓰기** — 템플릿 상태로 두면 에이전트가 엉뚱한 전제를 가져감
3. `context/phases/_template.md` 를 복사해 `context/phases/<phase>.md` 작성. 각 sub_goal 의 `**artifacts**:` 목록이 에이전트가 만들 파일 종류를 결정
4. 에이전트 루프(4a) 또는 CLI 직접 호출(4b) 로 배포 실행
5. (GitHub 연동 시) `git init` · `.gitignore` 에 `logs/`, `.claude/settings.local.json` 추가 후 첫 커밋. `.claude/` 본체는 팀 공용이니 커밋 대상

`harness/`, `docs/` 은 생기지 **않습니다** — 그건 kubeharness 레포 자체의 파일이고, 소비자 프로젝트는 `pip install` 된 패키지를 `python -m harness` 로 호출만 합니다. 템플릿 원본은 `harness/templates/` 로 패키지에 번들돼 있습니다.

## 에이전트 CLI 워크플로

`init` 은 slash command 1개(`/deploy`), subagent 2개(`deploy-orchestrator`, `runtime-diagnoser`), skill 5개, 그리고 권한/훅 wiring 까지 포함한 `.claude/` 트리를 함께 만들어 줍니다. Claude Code 에서:

```
/deploy <phase> <sub_goal>     # 예: /deploy observability prometheus
```

을 실행하면 orchestrator 가 `context/phases/<phase>.md` 의 해당 sub_goal 섹션에서 `service_name` 을 읽어 내고, verify-static → apply → verify-runtime 을 재시도 버짓과 함께 돌립니다. runtime 실패는 read-only 인 `runtime-diagnoser` 가 진단하고, 제안된 파일 수정은 **`ask` 권한**을 거치므로 모든 Write 마다 사람 승인 프롬프트가 뜹니다. 전체 규약은 `init` 이 생성하는 `AGENTS.md` 에 정리돼 있습니다.

Codex CLI 등 다른 에이전트 러너는 같은 `AGENTS.md` 를 읽습니다. `.claude/` 트리는 **Claude Code 전용 wiring 예시**이니, 다른 CLI 를 쓰면 무시하세요.

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
