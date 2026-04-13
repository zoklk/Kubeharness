# Phase: `messaging`

## Phase 개요
- **목적**: ESP32 디바이스가 MQTT over TLS(8883)로 EMQX 3-Pod HA 클러스터에 연결되며, Cilium L2 Announcements VIP가 단일 캠퍼스 진입점을 제공하는 상태
- **관련 기술**: EMQX 5.8.6 (StatefulSet), Cilium 1.19.2 (L2 Announcements, Beta)

## Sub_goals 목록
| # | ID | service_name | 요약 |
|---|---|---|---|
| 1 | `emqx` | `emqx` | EMQX 5.8.6 StatefulSet 3 Pod, DNS 디스커버리 HA 클러스터 |
| 2 | `cilium-l2-vip` | `emqx-lb` | Cilium L2 Announcements VIP, 단일 LoadBalancer IP ARP 광고 |

---

## Sub_goal: `emqx`
- **service_name**: `emqx`
- **technology**: `emqx`

### 1. 목표 사양
- **기능**: EMQX 5.8.6 3-Pod StatefulSet HA 클러스터를 `{NAMESPACE}` 네임스페이스에 구축.
- **디스커버리**: K3s DNS 기반의 정적 클러스터 디스커버리 사용.
- **스케줄링**: 각 Pod는 하드웨어 장애 대비를 위해 서로 다른 노드에 배치(Anti-affinity).

### 2. 인터페이스
- **Namespace**: `{NAMESPACE}`
- **Port**:
  - `mqtt: 1883` — 평문, 내부망 (mTLS 설정 전 검증용)
  - `dashboard: 18083` — EMQX Dashboard API
  - `ekka: 4370` — EMQX 클러스터 내부 RPC
- **Labels**: `app.kubernetes.io/name: emqx`
- **dependency**: 없음
- **리소스**:
  - CPU: `200m` / `500m`
  - Memory: `384Mi` / `512Mi` (개발 alpha 클러스터)

### 3. Smoke Test
- **경로**: `edge-server/tests/messaging/smoke-test-emqx.sh`

### 4. 제약사항
- anti-affinity를 `required`로 설정해 3 Pod가 반드시 다른 노드에 배치되어야 함.

---

## Sub_goal: `cilium-l2-vip`
- **service_name**: `emqx-lb`
- **technology**: `cilium`

### 1. 목표 사양
- **기능**: Cilium L2 Announcements를 통해 EMQX 클러스터 앞단에 단일 VIP(LoadBalancer IP) 구성.
- **의존성**: `emqx` 서비스가 선행 배포되어 있어야 함.

### 2. 인터페이스
- **Namespace**: `{NAMESPACE}` (Service), `kube-system` (Cilium Config)
- **Ports**: 1883 (MQTT), 8883 (MQTTS)
- **Labels**: `app.kubernetes.io/name: emqx` (Service selector 연동)
- **dependency**: `emqx`

### 3. Smoke Test
- **경로**: `edge-server/tests/messaging/smoke-test-cilium-l2-vip.sh`

## Phase 완료 기준
- 모든 Sub_goal의 Smoke Test 성공
