# Harness 내부 동작 구조

> phase 문서 작성자용. LLM에게는 전달되지 않음.
> 이 문서를 모르면 phase 문서가 하네스 동작과 어긋나는 내용을 포함하게 됨.

---

## 0. 핵심 철학

GikView 하네스는 **쿠버네티스(Kubernetes) 방식의 선언적 관리**를 LLM 기반 개발 자동화에 적용한다.

### 선언적 목표 명시 (Phase 문서)

- 각 Phase 문서(`context/phases/<phase>.md`)는 "완료 상태"를 선언적으로 기술한다.
- "어떻게(How)"가 아닌 "무엇(What)"을 명시: 포트, 레이블, 리소스 제약, 의존 관계.
- Developer 노드는 이 선언을 읽고 스스로 구현 경로를 결정한다.

### 제약조건 = Smoke Test

- Phase 문서의 제약사항은 자유 텍스트가 아니라 smoke test 스크립트로 기계 검증된다.
- smoke test는 **수용 기준(acceptance criterion)**: 통과해야만 sub_goal 완료.
- "문서에 명시됐지만 smoke test가 없는 제약"은 강제되지 않는다.

### 결과 기반 피드백 루프

- Static / Runtime Verifier는 Developer의 출력물을 검증하고 실패 원인을 구조화된 형식으로 반환한다.
- Runtime Verifier의 LLM 진단(Phase 2) 결과는 `context/knowledge/<tech>-llm-findings.md`에 자동 기록되어 **동일 Phase 2 실행 중** 및 **다음 시도(Developer 노드)** 모두에 주입된다.
- 사람이 개입하지 않아도 반복 시도를 통해 수정 정확도가 높아진다.

> 요약: **Phase 문서**로 목표를 선언하고, **smoke test**로 제약을 강제하며, **피드백 루프**로 점진적으로 수렴한다.

---

## 1. 그래프 흐름

```
[START]
  └─► developer ──► static_verifier ──┬─(pass)─► runtime_verifier ──┬─(pass)─► END
        ▲                             └─(fail)──────────────────────┘          │
        └────────────────────────────────────────────────────────(fail)─────────┘
```

**인터럽트 지점**
- `interrupt_before["developer"]`: developer 실행 직전. run.py가 사람 입력 수신 후 resume.
- `interrupt_after["runtime_verifier"]`: runtime_verifier 실행 직후. 사람이 결과 확인 후 계속/중단.

---

## 2. HarnessState 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `current_phase` | str | CLI `--phase` 값 |
| `current_sub_goal` | SubGoal | `{name, phase, stage, service_name}` |
| `dev_artifacts` | dict | `{"files": [경로, ...], "notes": "..."}` |
| `static_verification` | dict | `{"passed": bool, "checks": [...]}` |
| `runtime_verification` | dict | `{"runtime_phase1": ..., "runtime_phase2": ...}` |
| `verification` | dict | 통합 결과. 라우팅 판단에 사용 |
| `error_count` | int | static/runtime fail 누적 횟수 |
| `user_hint` | str | developer interrupt에서 입력한 추가 지시. developer 실행 후 소거 |
| `sub_goal_spec` | str | phase.md에서 추출한 sub_goal 섹션. runtime_verifier Phase 2에서 재사용 |
| `technology_name` | str | sub_goal spec의 `**technology**:` 필드. 없으면 service_name 폴백. knowledge 파일 조회 키 |

---

## 3. Developer 노드

### 입력 컨텍스트 (LLM에게 전달되는 정보)

LLM은 다음을 **user message**로 받음:

| 섹션 | 출처 | 내용 |
|------|------|------|
| Target | state | phase, sub_goal name |
| Conventions | `context/base/conventions.md` | 파일 경로 규칙, 릴리스 이름 등 |
| Tech Stack | `context/base/tech_stack.md` | 버전, 컴포넌트 정보 |
| Cluster Environments | `config/cluster.yaml` → Python 코드 주입 | active 환경, dev/prod domain_suffix, arch |
| Sub-Goal Specification | `context/phases/<phase>.md` 에서 sub_goal 섹션 추출 | 목표 사양, 인터페이스, 제약사항 |
| Existing Files | `edge-server/{helm,manifests,docker,ebpf}/<service_name>/` 스캔 | 파일 경로 목록만 주입. 내용은 `read_file` 툴로 조회 |
| Smoke Tests | `edge-server/tests/<phase>/smoke-test-<sub_goal>.sh` | 파일 전체 내용 직접 주입. 없으면 생략. |
| Dependency Services | sub_goal spec의 `dependency` 필드 파싱 | 의존 서비스명 목록 + kagent로 직접 조회 안내 |
| Technology Knowledge | `context/knowledge/<technology_name>.md` + dep knowledge | 기술 기반 지식 (고신뢰). 없으면 생략. |
| Previous Diagnostic Findings | `context/knowledge/<technology_name>-llm-findings.md` + dep findings | Runtime Verifier 자동 기록 (저신뢰). 없으면 생략. |
| Previous Verification Failure | state.verification | fail 체크 상세, LLM 제안 |
| Additional Instructions | state.user_hint | 사람이 interrupt에서 입력한 지시 |

**LLM에게 없는 정보**: cluster.yaml 직접 읽기 불가. 단, Cluster Environments 섹션에 active env, domain_suffix, arch가 주입되므로 이를 활용해야 함.

### Cluster Environments 주입 예시

```
## Cluster Environments
Active for testing: `dev` (Static/Runtime Verifier will use `values-dev.yaml`)

You MUST write `values-dev.yaml` AND `values-prod.yaml` for EVERY service.

| env  | domain_suffix         | arch  |
|------|-----------------------|-------|
| dev  | alpha.nexus.local     | amd64 |
| prod | cluster.local         | arm64 |

### `dev` (`values-dev.yaml`)
- domain_suffix: `alpha.nexus.local`
- arch: `linux/amd64`
- DNS example: `<service>-headless.gikview.svc.alpha.nexus.local`

### `prod` (`values-prod.yaml`)
- domain_suffix: `cluster.local`
- arch: `linux/arm64`
- DNS example: `<service>-headless.gikview.svc.cluster.local`
```

→ **node_storage, node_monitoring 등 환경별 다른 값은 phase 문서에 직접 기재하거나 cluster.yaml에 추가한 뒤 `harness/config.py`의 `build_cluster_env_section()`에도 추가해야 함.** Developer와 Runtime Verifier 양쪽이 이 함수를 공유하므로 한 곳만 수정하면 된다.

### LLM 출력 형식

```json
{
  "files": [
    {"path": "edge-server/helm/<service>/Chart.yaml", "content": "..."},
    {"path": "edge-server/helm/<service>/values.yaml", "content": "..."},
    {"path": "edge-server/helm/<service>/values-dev.yaml", "content": "..."},
    {"path": "edge-server/helm/<service>/values-prod.yaml", "content": "..."}
  ],
  "notes": "..."
}
```

### Tool loop

최대 **20턴**. 초과 시 tools 없이 최종 응답 요청.

### 사용 가능 툴

- **kagent MCP** (`developer_tools`): `GetResources`, `GetResourceYAML`, `DescribeResource`, `GetPodLogs`, `GetRelease` 등 (read-only)
- **`read_file`** (로컬): `edge-server/` 하위 파일 읽기. MCP 불필요. Existing Files 목록의 파일 내용 조회용.

### 허용 경로 (prefix guard)

`edge-server/` 로 시작하지 않으면 드롭(경고 출력).

### 아티팩트 감지 경로

| 유형 | 경로 | 정적 검사 대상 |
|------|------|---------------|
| Helm chart | `edge-server/helm/<service_name>/` | yamllint, helm lint, helm template\|kubeconform, trivy, gitleaks, helm dry-run server |
| Raw manifest | `edge-server/manifests/<service_name>/` | yamllint, kubeconform, trivy, gitleaks, kubectl dry-run server |
| Docker (소스+Dockerfile) | `edge-server/docker/<service_name>/` | hadolint, gitleaks |
| eBPF | `edge-server/ebpf/<service_name>/` | 정적 검사 없음 (사람이 직접 관리) |

---

## 4. Static Verifier 노드

LLM 없음. 결정적 실행.

### 실행 체크 순서

1. `path_prefix`: `edge-server/` prefix 위반 파일 감지 (fail이어도 계속 진행)
2. Helm 감지 시: yamllint → helm lint → helm template|kubeconform → trivy config → gitleaks → helm dry-run server
3. manifest 감지 시: yamllint → kubeconform → trivy config → gitleaks → kubectl dry-run server
4. docker 감지 시: hadolint → gitleaks
5. 아무것도 감지 안 되면 `artifact_detection` fail

### values 파일 선택

`values.yaml` + `values-{active}.yaml` (active = cluster.yaml의 `active` 필드)

→ **`values-dev.yaml`을 active로 테스트하므로, 이 파일에 dev 환경 nodeSelector 등을 명시해야 helm dry-run/lint가 올바른 값으로 검증됨.**

### 라우팅

- 모든 check `pass` 또는 `skip` → runtime_verifier
- 하나라도 `fail` → developer (error_count 증가)

---

## 5. Runtime Verifier 노드

### Phase 1 (결정적 게이트)

순서대로 실행. 하나 fail이면 이후 체크는 skip하고 즉시 반환. `kubectl_wait` 실패 시 smoke_test는 skip.
이벤트 조회는 Phase 1에서 하지 않음 — Phase 1 fail 시 Phase 2 LLM이 kagent로 직접 수행.

| 단계 | 조건 | 동작 |
|------|------|------|
| docker build+push | `edge-server/docker/<service>/Dockerfile` 존재 | `config/build.yaml`의 `registry`로 빌드 후 푸시 |
| helm upgrade --install | `edge-server/helm/<service>/` 존재 | `--wait` 없음 (빠른 적용) |
| kubectl apply | `edge-server/manifests/<service>/` 존재 (helm 없을 때) | |
| kubectl wait pods | helm 경로일 때만 | **2단계**: 60s 대기 → terminal 상태 감지 → terminal이면 즉시 fail / 아니면 240s 추가 대기 |
| smoke test | `edge-server/tests/<phase>/smoke-test-<sub_goal>.sh` 존재 시 | kubectl_wait 실패 시 skip |

**values 파일**: `values.yaml` + `values-{active}.yaml` (static_verifier와 동일 로직)

**immutable field 복구**: helm fail 시 아래 조건 중 하나 → helm uninstall → 재설치
- stderr/stdout에 `"immutable"` 포함 (일반적인 k8s immutable field 오류)
- stderr/stdout에 `"forbidden"` AND `"statefulset spec"` 포함 (volumeClaimTemplates 변경 등)

**kubectl wait 2단계 + stale revision 복구 상세**:
- 60s 경과 후 pod 상태 확인 (`kubectl get pods -o json`)
- terminal 상태(`CrashLoopBackOff`, `ImagePullBackOff`, `ErrImagePull`, `Error`, `OOMKilled` 등): 즉시 fail (조기 종료)
- 기동 중(`Pending`, `Init`, `ContainerCreating` 등): 240s 추가 대기 (총 최대 300s)
- **240s 실패 + not terminal + StatefulSet `updateRevision` 불일치** → `kubectl rollout restart statefulset/<service>` 후 300s 재대기 (check name: `rollout_restart_recovery`)

**주의**: Helm은 `--wait` 없이 실행 (빠른 적용). 파드 Ready 게이트는 `kubectl wait` 2단계.

### Phase 2 (LLM 진단)

**Phase 1 fail 시에만 실행** (Phase 1이 완전 통과하면 LLM 불필요).

- kagent 도구: 읽기 전용 (`GetPodLogs`, `DescribeResource`, `GetEvents` 등)
- 역할: 실패 원인 진단. pod 로그, 이벤트, describe로 root cause를 파악해 Developer에게 구체적 수정 지시 제공
- user message 주입 컨텍스트 (순서대로):

  | 섹션 | 출처 | 목적 |
  |------|------|------|
  | Sub-Goal Specification | `state.sub_goal_spec` (developer가 캐시) | 목표 사양 |
  | Cluster Environments | `build_cluster_env_section(include_authoring_hint=False)` | 활성 env, domain_suffix — DNS 진단에 올바른 도메인 사용하도록 |
  | Phase 1 Results | `run_runtime_phase1()` 결과 | 실패한 체크 상세 |
  | Artifact Files | `edge-server/{helm,manifests,docker}/<service>/` 스캔 | 정확한 파일 경로 참조 |
  | Technology Knowledge | `context/knowledge/<tech>.md` + dep knowledge (`harness.llm.context.read_knowledge`) | 기술 기반 지식, 환경별 설정값 (없으면 생략) |
  | Previous Diagnostic Findings | `context/knowledge/<tech>-llm-findings.md` + dep findings (`harness.llm.context.read_knowledge`) | 과거 진단 결과 힌트 (없으면 생략) |
- 제안 형식: 파일 경로 + 정확한 YAML key + before→after 값 명시
- 응답: `{"passed": false, "observations": [...], "suggestions": [...]}`
- `passed`는 항상 `false` (Phase 1이 실패했으므로)
- Tool loop 최대 **10턴**. 초과 시 tools 없이 최종 응답 요청.
- **Findings 저장**: 응답은 `context/knowledge/<tech>-llm-findings.md`에 저장. 동일 sub_goal 항목은 최신 3개만 유지 (`_MAX_FINDINGS_PER_SUBGOAL = 3`). 다른 sub_goal 항목은 보존.

### 라우팅

- Phase 1 전부 통과 (smoke test 포함) → END (`verification.passed = true`)
- Phase 1 fail → Phase 2 LLM 진단 → developer (`verification.passed = false`, error_count 증가)

---

## 6. Phase 문서 파싱 규칙

developer 노드가 `context/phases/<phase>.md`에서 sub_goal 섹션을 추출하는 방법:

- **Fuzzy matching**: 헤딩(# ~ ####)의 텍스트에 `sub_goal name`이 포함되면 매칭. 대소문자 무시.
  - 예) `## 1. emqx 설치` → `"emqx"` 키워드로 매칭 성공
- 매칭된 헤딩과 동일 레벨 이상의 다음 헤딩 직전까지 섹션으로 추출. 하위 헤딩(###)은 포함.
- 못 찾으면 파일 전체를 반환.
- `**service_name**:` 필드 → helm/manifest/docker/smoke 경로의 기준
- `**technology**:` 필드 → knowledge 파일 조회 키 (`context/knowledge/<technology>.md`). 없으면 service_name 폴백.
- `**dependency**:` 필드 → 의존 서비스명 파싱 (`` `name` `` 형식)
- service_name 못 찾으면 sub_goal name으로 폴백

---

## 7. 환경별 분리 패턴 (values 파일)

LLM이 cluster.yaml을 직접 읽지 못하므로, 환경별 다른 값은 values 파일로 분리.

```
edge-server/helm/<service>/
  values.yaml          ← 공통 기본값
  values-dev.yaml      ← dev 오버라이드 (nodeSelector: alpha-w3 등)
  values-prod.yaml     ← prod 오버라이드 (nodeSelector: e-s3 등)
```

하네스는 `active` 환경의 values 파일을 자동 선택해 helm에 주입.
LLM에게 "MUST write values-dev.yaml AND values-prod.yaml" 지시가 user message에 포함됨.

### 환경별 노드 고정 값 (cluster.yaml 기준)

| 서비스 | dev 노드 | prod 노드 |
|--------|---------|----------|
| InfluxDB (`node_storage`) | `alpha-w3` | `e-s3` |
| Prometheus / Grafana (`node_monitoring`) | `alpha-w2` | `e-s2` |

phase 문서에 이 값을 명시하면 LLM이 values 파일에 올바른 nodeSelector를 작성함.

---

## 8. 릴리스 이름 / 레이블 / 네임스페이스 컨벤션

| 항목 | 값 |
|------|----|
| 네임스페이스 | `gikview` |
| Helm release name | `<service_name>-dev-v1` |
| kubectl label selector | `app.kubernetes.io/name=<service_name>` |
| Smoke test 경로 | `edge-server/tests/<phase>/smoke-test-<sub_goal>.sh` |

---

## 9. Phase 문서 작성 체크리스트

phase 문서가 하네스와 어긋나는 경우 체크:

- [ ] `service_name`이 helm/manifest/docker 경로의 `<service_name>`과 일치하는가
- [ ] `**technology**:` 필드가 명시되어 있는가 (service_name과 다를 경우 필수. 예: service=cilium-l2, tech=cilium)
- [ ] 기술 일반 지식(동작 원리, 설정 예시, 버전 근거)은 phase 문서가 아닌 `context/knowledge/<tech>.md`에 작성했는가
- [ ] 커스텀 이미지 서비스는 `edge-server/docker/<service_name>/` 경로를 명시했는가
- [ ] 환경별 다른 값(nodeSelector 등)은 values-dev.yaml / values-prod.yaml 분리로 설명했는가
- [ ] smoke test 경로가 `edge-server/tests/<phase>/smoke-test-<sub_goal>.sh`인가
- [ ] dependency 필드가 `` `service_name` `` 형식(백틱)으로 작성됐는가
- [ ] kubectl wait timeout이 `300s`인가 (helm 배포 기준)
- [ ] Helm release name이 `<service_name>-dev-v1`인가
