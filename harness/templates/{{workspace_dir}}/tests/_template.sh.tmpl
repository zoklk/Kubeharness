#!/usr/bin/env bash
# smoke-test-<service>.sh
# Phase  : <phase>
# Service: <service>
#
# 배치 경로: {{workspace_dir}}/tests/<phase>/smoke-test-<service>.sh
# (harness.yaml 의 conventions.smoke_test_path 포맷에 맞춰야 runtime 이 발견)
#
# 주입 환경변수 (runtime 이 제공):
#   SERVICE         — 서비스 slug
#   NAMESPACE       — 배포 대상 네임스페이스
#   RELEASE_NAME    — helm release 이름
#   ACTIVE_ENV      — 현재 환경 (dev / prod / ...)
#   DOMAIN_SUFFIX   — 환경별 도메인 suffix
#
# 반환값 규칙 (Runtime Verifier 가 강제):
#   exit 0   → smoke_test=pass
#   exit 비0 → smoke_test=fail  (stdout+stderr 가 detail 로 기록됨)
#
# 실패 메시지 형식 (runtime-diagnoser 진단 비용을 좌우):
#   각 assertion 은 실패 시 stdout 에 다음 두 줄을 남긴 뒤 exit 1.
#
#     echo "FAIL: <섹션 라벨>: <짧은 사유>"
#     echo "  actual: <실제 관측값>"   # 응답 본문, kubectl 출력, 마지막 시도 결과 등
#
#   이 두 줄이 verify_runtime_response.checks[].log_tail (2000자) 안에
#   들어가서 diagnoser 의 1차 단서가 됩니다. 이걸 안 남기면 어떤
#   assertion 이 왜 실패했는지 알 수 없어 cluster sweep 으로 강등되고,
#   그 비용은 호출자(/deploy 사용자) 가 토큰으로 부담합니다.
#
# 실행시간 규칙:
#   - 스크립트 전체 실행시간 120s 이내
#   - retry loop 은 (반복횟수 × sleep 간격) ≤ 120s 로 설계
#   - kubectl_wait (최대 300s) 이후 실행되므로 pod Ready 는 보장된 상태에서 시작
#
# port-forward 정리 규칙:
#   - 백그라운드 port-forward 는 반드시 trap 으로 정리
#   - 모든 PID 를 시작 직후 trap 에 한 번에 등록 (중간 exit 시 누수 방지)

set -euo pipefail
NS="${NAMESPACE:?NAMESPACE env not injected by runtime}"

# ── port-forward (필요 없으면 삭제) ──────────────────────────────────────────
# port-forward 가 1개인 경우:
#
#   kubectl port-forward -n "$NS" svc/<service> <local>:<remote> &
#   PF_PID=$!
#   sleep 2
#   trap "kill $PF_PID 2>/dev/null" EXIT
#
# port-forward 가 여러 개인 경우 — 모두 시작한 뒤 trap 을 한 번에 등록:
#
#   kubectl port-forward -n "$NS" svc/<service> <local1>:<remote1> &
#   PF1_PID=$!
#   kubectl port-forward -n "$NS" svc/<service> <local2>:<remote2> &
#   PF2_PID=$!
#   sleep 2
#   trap "kill $PF1_PID $PF2_PID 2>/dev/null" EXIT

# ── retry loop (필요 없으면 삭제) ─────────────────────────────────────────────
# 최대 대기시간: RETRIES × INTERVAL ≤ 120s
#
#   RETRIES=6
#   INTERVAL=10
#   RESULT=""
#   for i in $(seq 1 $RETRIES); do
#     RESULT=$(some_command 2>&1 || true)
#     [ -n "$RESULT" ] && break
#     echo "attempt $i/$RETRIES: not ready, waiting ${INTERVAL}s..."
#     sleep $INTERVAL
#   done
#   [ -n "$RESULT" ] || {
#     echo "FAIL: <항목>: not ready after $((RETRIES * INTERVAL))s"
#     echo "  actual: <last attempt output or 'no response'>"
#     exit 1
#   }

# ── "actual" 패턴 가이드 ──────────────────────────────────────────────────────
# 변수 이름은 무관. assertion 의 비교 대상이 되는 관측값을 "  actual:" 다음에
# 그대로 출력하면 됩니다. 테스트 종류에 따라 어떤 값을 적을지 달라집니다 —
# 아래 패턴 중 가장 가까운 걸 골라 쓰세요.
#
# (a) HTTP body grep:
#     RESULT=$(curl -sf "$URL" 2>&1 || true)
#     echo "$RESULT" | grep -q "<expected>" || {
#       echo "FAIL: <항목>: response did not contain '<expected>'"
#       echo "  actual: $RESULT"
#       exit 1
#     }
#
# (b) HTTP status code:
#     STATUS=$(curl -s -o /tmp/body -w "%{http_code}" "$URL")
#     [ "$STATUS" = "200" ] || {
#       echo "FAIL: <항목>: expected HTTP 200"
#       echo "  actual: HTTP $STATUS, body=$(cat /tmp/body 2>/dev/null || echo '')"
#       exit 1
#     }
#
# (c) kubectl + jq (자원 수, status field 등 구조화된 결과):
#     COUNT=$(kubectl get pod -n "$NS" -l app="$SERVICE" -o json \
#       | jq '[.items[] | select(.status.phase=="Running")] | length')
#     [ "$COUNT" -ge 3 ] || {
#       echo "FAIL: <항목>: expected >=3 Running pods"
#       echo "  actual: $COUNT pods"
#       exit 1
#     }
#
# (d) multi-step: 각 단계별 출력을 따로 캡처하고, 실패한 단계의 출력만
#     "actual:" 에 담을 것. e.g. login 토큰 발급 실패 → 토큰 응답 본문을
#     출력. 후속 API 호출 실패 → 그 호출의 응답 본문을 출력.

# ── 1. <검증 항목> ────────────────────────────────────────────────────────────
# (위 (a)-(d) 패턴 중 하나를 골라 작성)

# ── 2. <검증 항목> ────────────────────────────────────────────────────────────
# (위 (a)-(d) 패턴 중 하나를 골라 작성)
