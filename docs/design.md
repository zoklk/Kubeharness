# Kubeharness — 설계

`kubeharness` 패키지의 내부 아키텍처. 읽는 순서: 목표 → 모듈 책임 → 데이터 흐름 → 확장 지점. 일상적인 사용법은 루트의 `README.md` 를 보세요.

이 문서는 리팩터링 이전 LangGraph 기반 harness 를 서술하던 `context/docs/harness_internals.md` 를 대체합니다.

---

## 1. 설계 목표

1. **결정적 CLI, 내부에 LLM 없음.** harness 는 검증 가능한 부수 효과(체크 실행, 배포 실행, 파드 대기)만 소유한다. 모든 판단은 외부 에이전트 CLI(Claude Code, Codex)에서 일어난다.
2. **파이프라인 단계 기준 배치.** 파일을 *어떤 도구* 가 아니라 *언제 실행되느냐* 로 묶는다. 배포 전 → `static.py`, 배포 · 배포 후 → `runtime.py`. 유지보수자가 "배포 후 돌아가는 체크"를 찾을 때 파일 하나만 열면 된다.
3. **도구 중립성.** 파이썬 CLI, `config/harness.yaml`, `AGENTS.md`, hook 스크립트는 어떤 에이전트 CLI 에서도 동작한다. `.claude/` 는 Claude Code 전용 wiring 예시일 뿐 런타임 의존성이 아니다.
4. **프로젝트 값은 하드코딩 금지.** namespace, release naming, chart path, image tag, 체크 enabled 목록, 타임아웃 — 소비자별로 달라지는 값은 **전부 `config/harness.yaml` 한 곳**. 코드 · skill · hook · slash command 모두 이 YAML 을 읽는다.
5. **한 번의 `/deploy` = 하나의 로그 파일.** 배포 사이클 동안 모든 subprocess 는 단일 `$HARNESS_SESSION_LOG` 에 append 한다. 도구별 개별 로그 파일은 안티 피처.
6. **최소주의.** 빈 스캐폴딩 · 선행 추상화 · "혹시 필요할지 몰라" 모듈 없음. 파이썬 파일은 딱 6개, 책임 1개당 1개.

---

## 2. 모듈 책임

```
harness/
├── __init__.py
├── __main__.py     # 얇은 진입점 → cli.main
├── config.py       # YAML 스키마, resolve(service), env 조회, @lru_cache
├── shell.py        # 유일한 subprocess.run; 세션 로그 append
├── static.py       # 배포 전 체크 + 레지스트리 + 감지 게이트
├── runtime.py      # apply + verify_runtime (kubectl wait, smoke test)
├── cli.py          # argparse, JSON 응답, exit code, subcommand 디스패치
└── init.py         # 템플릿 스캐폴드, {{var}} 치환
```

총 6개. 변경사항이 이 중 하나에 깔끔히 들어맞지 않으면, 변경의 모양 자체가 잘못됐을 가능성이 높다.

### `config.py`

`config/harness.yaml` 을 타입이 잡힌 dataclass 로 파싱하고 치환 프리미티브를 노출한다. 주요 API:

```python
cfg = load_config()                             # @lru_cache
cfg.resolve("prometheus").release_name          # "prometheus-dev-v1"
cfg.resolve("prometheus").chart_path            # Path("workspace/helm/prometheus")
cfg.resolve("prometheus").values_files()        # [Path("values.yaml"), Path("values-dev.yaml")]
cfg.env("dev").domain_suffix                    # environments.dev 에서
cfg.active_environment()                        # 축약 — env(cfg.active_env)
cfg.smoke_test_path("svc", "p1", "svc")         # Path("workspace/tests/p1/smoke-test-svc.sh")
cfg.checks.static.is_enabled("yamllint")        # bool
cfg.checks.runtime.kubectl_wait.initial_wait_seconds  # int
```

여기서 풀리는 치환 토큰: `{service}`, `{active_env}`, `{workspace}`, `{phase}`, `{sub_goal}`.

기본값(refactor.md §9): YAML 이 `release_name`, `image_tag`, `kubectl_wait.initial_wait_seconds` 등을 생략하면 기본값이 채워진다. `config.py` 의 `_parse` 참조.

### `shell.py`

모든 subprocess 의 단일 진입점. 패키지 내 다른 모든 모듈(static 체크, helm 호출, kubectl wait)은 `shell.run` 또는 `shell.pipe` 를 거친다. 이 모듈이 소유하는 것:

- **timeout** (기본 120s, 호출별 override)
- **세션 로그 append** — `HARNESS_SESSION_LOG` 가 설정돼 있으면 매 호출이 아래 블록을 append:
  ```
  --- [label] $ cmd ---
  <stdout>
  <stderr>
  [exit N] (duration: 1.23s)
  ```
- **CLI 미설치 처리** — 예외를 던지는 대신 `exit_code=-1` + stderr `"command not found: X"` 를 반환. 호출자가 exception 핸들링 없이 `skip` 으로 분류할 수 있다.
- **PATH 보강** — `~/.local/bin` 을 prepend 해서 `pip install --user` 나 언어별 패키지 매니저가 깐 도구를 찾도록.

`shell.pipe(a, b)` 는 같은 로깅 규약으로 `a | b` 를 실행한다 — `helm template | kubeconform` 에서 사용.

### `static.py`

체크 함수 8개, 각각 `(ResolvedService, Config) → CheckResult`. 아티팩트 종류별 레지스트리로 묶인다:

```
HELM_CHECKS   = {yamllint, helm_lint, kubeconform, trivy_config,
                 gitleaks, helm_dry_run_server}
DOCKER_CHECKS = {hadolint, gitleaks_docker}
```

공개 진입점은 `run_static(service, cfg)`:

1. 아티팩트 감지: `chart_path.is_dir()` 과/또는 `docker_path/Dockerfile`.
2. 아무것도 없으면 `artifact_detection` fail 한 건 반환.
3. 매칭되는 그룹의 enabled 체크를 차례로 호출.
4. `list[CheckResult]` 반환 — 예외 없음, short-circuit 없음.

`CheckResult` 는 frozen dataclass: `name`, `status` ∈ {pass, fail, skip}, 선택 `detail`(한 줄 요약), 선택 `log_tail`(stdout+stderr 합본 끝 N 자, N = `logging.tail_chars`).

### `runtime.py`

공개 진입점 2개. 둘 다 `static.py` 와 동일한 방식으로 감지 게이팅된다.

**`apply(service, cfg)`** — 배포 흐름:

1. `docker/<svc>/Dockerfile` 존재 시: `docker build --platform linux/{arch}` (arch 는 active env 에서) → `docker push`. build 실패 시 중단, 이후 단계는 `skip`.
2. `helm/<svc>/` 존재 시:
   a. `helm status <release> -n <ns>` 로 기존 릴리스 감지.
   b. 존재하면 `helm uninstall` 먼저. 이전 chart 에 immutable field(예: `spec.selector`)가 있어 `helm upgrade` 가 거부하는 흔한 케이스를 처리한다. clean uninstall 후 install 이 가장 단순한 결정적 해법.
   c. `helm upgrade --install --create-namespace --timeout 60s` + 모든 `values.yaml` + `values-<active_env>.yaml` 을 `-f` 로 주입.

`helm upgrade` 에 **`--wait` 없음**. 파드 readiness 는 더 풍부한 신호를 볼 수 있는 `verify_runtime` 책임.

**`verify_runtime(service, cfg, *, phase, sub_goal)`** — 배포 후:

1. **CRD-only chart 감지.** `helm template` 을 돌려 렌더된 YAML 을 파싱. `kind` 가 `{Deployment, StatefulSet, DaemonSet, ReplicaSet, Job, CronJob, Pod}` 중 하나도 없으면 CRD-only chart 로 간주해 `kubectl_wait` 를 skip. template 실패 시엔 "workload 있음"으로 보수적으로 처리 — 레거시 동작 보존.

2. **2단계 kubectl wait.** 2-phase wait 패턴:
   - 1차 probe: `kubectl wait pods --for=condition=Ready --timeout=<initial_wait_seconds>s`
   - timeout 나면 `kubectl get pods -o json` 으로 파드 상태 확인. 컨테이너 중 하나라도 `_TERMINAL_STATES` 사유(CrashLoopBackOff, ImagePullBackOff, Error, OOMKilled, …)면 **즉시 fail**. 더 기다릴 이유 없음.
   - Pending / ContainerCreating 수준이면 `terminal_grace_seconds` 만큼 두 번째 wait — 느린 이미지 풀러나 복잡한 initContainer 에 시간을 더 준다.

   기본값: 1차 60s, grace 240s (프로젝트별로 `checks.runtime.kubectl_wait.*` 에서 조정).

3. **Smoke test.** `cfg.smoke_test_path(service, phase, sub_goal)` 이 존재하면 `bash <path>` 로 실행하며 다음 env 주입:
   - `SERVICE`, `NAMESPACE`, `RELEASE_NAME`, `ACTIVE_ENV`, `DOMAIN_SUFFIX`

   `phase` 또는 `sub_goal` 이 누락되면 (CLI 플래그를 안 넘긴 경우) skip — 어느 smoke test 가 서비스에 맞는지 CLI 가 추측할 수 없다.

### `cli.py`

argparse 기반, 서브커맨드 4개: `init`, `verify-static`, `apply`, `verify-runtime`. 각 verify/apply 커맨드는:

- config 로드 (`ConfigError` 면 exit code `2`)
- caller 가 `$HARNESS_SESSION_LOG` 를 안 잡아뒀으면 기본값으로 설정
- 세션 시작 이벤트를 로그에 기록
- `static.run_static` / `runtime.apply` / `runtime.verify_runtime` 호출
- stdout 에 JSON 응답 방출:
  ```json
  {
    "service": "...",
    "stage": "verify-static",
    "summary": "3 passed, 1 failed, 0 skipped",
    "passed": false,
    "session_log": "logs/deploy/....log",
    "checks": [{"name": "...", "status": "...", "detail": "...", "log_tail": "..."}]
  }
  ```
- exit code: 전부 pass(또는 skip)면 `0`, 하나라도 fail 이면 `1`, config 오류는 `2`.

이 JSON 응답이 호출 에이전트와의 유일한 통신 채널. 중요한 건 stderr 로 흘리지 않는다 — 전부 세션 로그에 있다.

### `init.py`

stdlib 만 쓰는 템플릿 복사기. 설치된 패키지 상대경로에서 `templates/` 를 걸어 다니며 `--dest` 로 복사하고, 이름이 `.tmpl` 로 끝나는 파일에 3가지 치환을 적용:

- `{{project_name}}` ← `--name` (또는 `basename(dest)`)
- `{{workspace_dir}}` ← `--workspace` (기본 `workspace`)
- `{{kubeharness_version}}` ← `importlib.metadata.version("kubeharness")`

치환 후 `.tmpl` suffix 를 떼어낸다. `.tmpl` 이 아닌 파일은 그대로 복사. 기존 파일은 `--force` 없으면 건너뛴다.

**Jinja2 · 템플릿 엔진 의도적으로 미도입** — 위 3개 변수가 전체 surface area.

---

## 3. 데이터 흐름: 한 번의 `/deploy` 사이클

```
메인 세션 / 사용자
  │  /deploy <svc> 실행
  ▼
deploy-orchestrator  (subagent — LLM 이 여기 살아있음)
  │  HARNESS_SESSION_LOG = logs/deploy/<ts>-<svc>.log 설정
  │
  ├─►  python -m harness verify-static --service <svc>  ──►  stdout JSON
  │         └── shell.run 이 subprocess 출력을 세션 로그에 append
  │
  ├─►  python -m harness apply --service <svc>          ──►  stdout JSON
  │         └── docker build+push, helm uninstall+upgrade
  │
  ├─►  python -m harness verify-runtime --service <svc> --phase P --sub-goal G
  │         └── helm template 파싱, kubectl_wait_staged, smoke_test
  │
  │  runtime fail 시:
  ├─►  Task(subagent_type="runtime-diagnoser", prompt=<요약 + 로그 경로>)
  │         └── diagnoser 가 Read 로 세션 로그를 열람, kagent 쿼리,
  │             observations + proposed_files 를 JSON 으로 반환
  │
  │  proposed_files 가 비어있지 않으면:
  ├─►  각각에 대해 Write(...) — `ask` 권한으로 사람 승인 프롬프트 발생
  │
  │  retry_count += 1, verify-static 부터 루프 재진입 (max_runtime_retries 한도)
  │
  ▼
{passed, retries, session_log, last_stage, summary} 반환
```

모든 단계가 단일 세션 로그에 append. CLI JSON 은 포인터만 담고 본문 덤프는 하지 않는다.

---

## 4. 템플릿 치환 모델

타이밍이 다른 치환 2패스:

- **Init 시점(`{{var}}`)**. `python -m harness init` 을 소비자가 실행할 때 `harness/init.py` 가 **한 번** 풀어낸다. 결과는 소비자 레포에 정적으로 박힌다. 이 패턴을 쓰는 파일은 `.tmpl` 로 끝나야 하며 치환 후 suffix 가 제거된다. 스캐폴드 이후 바뀌지 않는 **프로젝트 정체성** 값(`{{project_name}}`, `{{workspace_dir}}`) 용.

- **런타임(`{service}`, `{active_env}`, `{workspace}`, …)**. `harness/config.py` 의 `resolve()` 안에서 **매 호출마다** 풀린다. 이 토큰들은 `config/harness.yaml` 에 리터럴 문자열로 박혀 있다. YAML 값을 바꾸면 다음 CLI 호출부터 바로 반영 — 스캐폴드를 다시 돌릴 필요 없다.

경험칙: 배포별 · env 별로 달라질 수 있으면 런타임 형식. 프로젝트 레이아웃에 박혀야 하면 init 형식.

---

## 5. 확장 지점

### static 체크 추가

1. `static.py` 에 `check_<name>(rs, cfg) -> CheckResult` 작성.
2. 소비하는 아티팩트에 따라 `HELM_CHECKS` 또는 `DOCKER_CHECKS` 에 등록.
3. `config/harness.yaml.example.tmpl` 에 enabled 플래그 추가.
4. `tests/test_static.py` 에 `_ShellStub` 패턴으로 테스트 추가.

체크는 **반드시** `shell.run` / `shell.pipe` 를 거쳐야 한다. 직접 `subprocess` 를 호출하면 세션 로그 규약이 깨진다.

### 새 environment 추가

1. `config/harness.yaml` 의 `environments.<env>: {domain_suffix, arch, node_selectors}` 에 추가.
2. `environments.active` 를 새 env 로 세팅(또는 CLI 플래그로 넘김 — refactor.md §20 에 따라 아직 미구현).
3. 각 chart 에 `values-<env>.yaml` 을 만든다.

코드 변경 불필요. `cfg.env(name)` 이 매트릭스를 제너릭하게 읽는다.

### 서브커맨드 추가

욕구를 참으시라. 4개(`init`, `verify-static`, `apply`, `verify-runtime`)가 직교하는 연산이고 상상할 수 있는 거의 모든 새 동사가 이것들의 조합이다. 정말 필요하다면 `cli._build_parser` 에 연결하되 핸들러는 재사용 헬퍼가 필요하지 않은 한 `cli.py` 안에 둔다.

---

## 6. 세션 로그 규약

- **경로**: `$HARNESS_SESSION_LOG`. env var 가 없으면 CLI 가 `logging.dir/<ts>-<service>-<stage>-standalone.log` 에 기본 파일을 만든다.
- **명령별 블록**:
  ```
  --- [label] $ <argv> ---
  <stdout, 비어있을 수 있음>
  <stderr, 비어있을 수 있음>
  [exit N] (duration: X.XXs)
  ```
- **이벤트 라인**(`shell.write_session_event`): 불투명한 문자열을 그대로 기록. CLI 와 orchestrator subagent 가 사용 (`=== deploy svc @ ts ===`, `[orchestrator] applied N file(s)`).
- **보관**: `logging.retention_days` 가 스키마에 선언돼 있지만 정리 로직은 아직 미구현(추후).

세션 로그가 포스트모템의 진본. CLI JSON 응답은 요약이고, 긴 출력은 `logging.tail_chars` 에 맞춰 잘린다. **로그 파일 자체는 절대 자르지 않는다.**

---

## 7. 의도적으로 없는 것

- **파이썬 내부 state machine · 재시도 루프 없음.** 재시도 사이클은 orchestrator subagent 책임. 이걸 파이썬으로 이식하고 싶어지는 순간이 **"LLM 은 바깥"** 경계가 깨지기 직전.
- **LLM API 클라이언트 없음.** `anthropic`, `openai`, `google-genai` 는 의존성 아님. 추가하는 건 기능 하나가 아니라 아키텍처 수준 변경.
- **MCP 클라이언트 없음.** `.claude/settings.json` 이 subagent 용 kagent MCP 엔드포인트를 선언. 파이썬 CLI 는 MCP 를 말하지 않는다.
- **플러그인 로더 없음.** 체크는 `HELM_CHECKS` / `DOCKER_CHECKS` 에 등록된 닫힌 집합. 소비자 커스터마이징은 `enabled` 플래그로만 하고, 익스텐션 로딩은 없다.

앞으로 이 경계선을 밀어붙이는 요구가 생기면 코드를 쓰기 전에 refactor.md 부터 다시 읽어보시라.
