<!--
════════════════════════════════════════════════════════════════════════════════
  HARNESS PHASE DOCUMENT — 작성 규칙
  이 파일을 작성하는 사람 또는 LLM은 반드시 읽을 것.
════════════════════════════════════════════════════════════════════════════════

이 파일은 하네스(Developer / Static Verifier / Runtime Verifier 노드)가
자동으로 읽고 처리하는 기계 파싱 대상입니다.
"보기 좋은 문서" 보다 "하네스가 오작동 없이 읽을 수 있는 규격"이 목표입니다.

─── A. 헤딩 구조 ─────────────────────────────────────────────────────────────
  · Phase 헤딩:    # Phase: <phase-name>    (파일 최상단, 1개)
  · Sub_goal 헤딩: ## Sub_goal: <id>        (각 sub_goal마다 1개)
  · <id>는 harness CLI --sub-goal 인수와 정확히 일치해야 합니다.
  · 하네스는 ## Sub_goal: <id> 헤딩부터 다음 ## 헤딩 직전까지를 sub_goal
    섹션으로 읽습니다 (_extract_subgoal_section).

─── B. service_name / technology 필드 ────────────────────────────────────────
  형식: - **service_name**: <값>   ← sub_goal 헤딩 바로 다음 첫 번째 줄
  형식: - **technology**: <값>     ← service_name 다음 줄 (service_name과 다를 경우 필수)

  · service_name과 기술명이 다를 수 있음. 예: service=cilium-l2, tech=cilium.
  · **technology** 필드가 없으면 service_name을 technology_name으로 사용.
  · technology_name은 context/knowledge/<tech>.md 조회 키로 사용됨.

  · 1 sub_goal = 1 service_name 규칙:
    하나의 sub_goal은 정확히 하나의 서비스만 생성하거나 수정합니다.
    기존 서비스를 수정하는 sub_goal은 그 서비스 이름을 service_name으로 씁니다.
    (예: emqx mTLS 설정 sub_goal → service_name: emqx)

  · 하네스가 service_name으로 결정하는 경로/이름:
    - 배포 경로:     edge-server/helm/<service_name>/
                     edge-server/manifests/<service_name>/
    - Helm release:  <service_name>
    - kubectl 셀렉터: app.kubernetes.io/name=<service_name>
    - Smoke test:    edge-server/tests/<phase>/smoke-test-<sub_goal>.sh
    - Docker build:  edge-server/docker/<service_name>/   (커스텀 이미지 시)
    - eBPF 소스:     edge-server/ebpf/<service_name>/     (eBPF 모듈 시)

─── C. 금지 항목 ─────────────────────────────────────────────────────────────
  · "권장", "예시로", "검토 필요" 등 모호한 표현 금지
  · 반드시 지켜야 하는 제약이 아닌 항목 기재 금지
  · 하나의 sub_goal 섹션에 두 개 이상의 service_name 금지
  · 섹션 번호와 이름 변경 금지
  · 기술 내부 동작 설명, 설정 YAML 예시, 이미지/버전 결정 근거, 기술 일반 제약은
    phase 문서 금지 → context/knowledge/<tech>.md 에 작성할 것

════════════════════════════════════════════════════════════════════════════════
-->

# Phase: `<phase-name>`

## Phase 개요
- **목적**: <이 Phase 완료 후 운영 시스템의 상태 — 1~2문장>
- **관련 기술**: <컴포넌트 이름 + 버전>

## Sub_goals 목록
| # | ID | service_name | 요약 |
|---|---|---|---|
| 1 | `<sub-goal-id>` | `<service-name>` | <한 줄 설명> |

---

## Sub_goal: `<sub-goal-id>`
- **service_name**: `<service-name>`
- **technology**: `<기술명>`  <!-- service_name과 다를 경우만 명시. 예: cilium-l2 서비스 → technology: cilium -->

### 1. 목표 사양
- **기능**: <무엇을 구현/변경하는가 — 1~3문장>
- **기술 스택**: <컴포넌트 이름 + 버전, Helm chart 버전 (repo URL 포함)>
- **배포 경로**: `edge-server/helm/<service-name>/` 또는 `edge-server/manifests/<service-name>/`
- **이미지**: `Docker Hub 공개 (<image>:<tag>)` 또는 `커스텀 빌드 (ghcr.io/<org>/<service-name>:<tag>)`

### 2. 인터페이스
- **Namespace**: `{NAMESPACE}`
- **Port**:
  - `<port-name>: <number>` — <용도>
- **Labels**: `app.kubernetes.io/name: <service-name>`
- **dependency**: `<service-name>`, `<service-name>` 또는 `없음`
  (서비스명 기준. 다른 phase 서비스도 동일하게 서비스명으로 명시)
- **리소스**:
  - CPU: `<request>` / `<limit>`
  - Memory: `<request>` / `<limit>`

### 3. Smoke Test
- **경로**: `edge-server/tests/<phase>/smoke-test-<sub-goal>.sh`

### 4. 제약사항
<!-- 이 환경/이 배포에 특화된 하드 제약만 기재. 기술 일반 제약(기술 자체 특성)은 context/knowledge/<tech>.md에 작성할 것. -->
- <반드시 지켜야 할 환경 특화 하드 제약. 없으면 이 섹션 전체 생략>

---

## Phase 완료 기준
- 모든 Sub_goal의 Smoke Test 성공
- <Phase 수준의 통합 검증 기준. 없으면 첫 줄만 유지>
