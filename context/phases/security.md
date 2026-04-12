# Phase: `security`

## Phase 개요
- **목적**: step-ca가 Intermediate CA로 동작하며 EST 엔드포인트를 제공해 ESP32 클라이언트 인증서 자동 갱신이 가능한 상태
- **관련 기술**: step-ca (Intermediate CA, EST)

## Sub_goals 목록
| # | ID | service_name | 요약 |
|---|---|---|---|
| 1 | `step-ca` | `step-ca` | step-ca Intermediate CA, EST 엔드포인트 |

---

## Sub_goal: `step-ca`
- **service_name**: `step-ca`

### 1. 목표 사양
- **기능**:
- **기술 스택**: step-ca
- **배포 경로**: `edge-server/helm/step-ca/`
- **이미지**: Docker Hub 공개 ()

### 2. 인터페이스
- **Namespace**: `gikview`
- **Port**:
- **Labels**: `app.kubernetes.io/name: step-ca`
- **dependency**: 없음
- **리소스**:
  - CPU:
  - Memory:

### 3. Smoke Test
- **경로**: `edge-server/tests/security/smoke-test-step-ca.sh`
- **검증**:
  1.

---

## Phase 완료 기준
- 모든 Sub_goal의 Smoke Test 성공
