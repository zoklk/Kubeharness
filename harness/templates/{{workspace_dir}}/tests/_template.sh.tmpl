#!/usr/bin/env bash
# smoke-test-<sub_goal>.sh
# Phase  : <phase>
# Sub-Goal: <sub_goal>
#
# 배치 경로: {{workspace_dir}}/tests/<phase>/smoke-test-<sub_goal>.sh
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
#     RESULT=$(some_command 2>/dev/null || true)
#     [ -n "$RESULT" ] && break
#     echo "attempt $i/$RETRIES: not ready, waiting ${INTERVAL}s..."
#     sleep $INTERVAL
#   done
#   [ -n "$RESULT" ] || { echo "FAIL: <항목> not ready after $((RETRIES * INTERVAL))s"; exit 1; }

# ── 1. <검증 항목> ────────────────────────────────────────────────────────────
# <command> || { echo "FAIL: <reason>"; exit 1; }

# ── 2. <검증 항목> ────────────────────────────────────────────────────────────
# <command> || { echo "FAIL: <reason>"; exit 1; }
