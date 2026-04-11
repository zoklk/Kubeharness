# Phase: `messaging`

## Phase 개요
- **목적**: ESP32 디바이스가 MQTT over TLS(8883)로 EMQX 3-Pod HA 클러스터에 연결되며, Cilium L2 Announcements VIP가 단일 캠퍼스 진입점을 제공하는 상태
- **관련 기술**: EMQX 5.8.6 (StatefulSet), Cilium 1.19.2 (L2 Announcements, Beta)

## 이미지 버전 결정 근거

> **채택: `emqx/emqx:5.8.6`**
>
> v5.9.0부터 BSL 1.1로 라이선스 변경. 1노드 초과 클러스터에 라이선스 키 필수.
> 5.8.6(Apache 2.0, 클러스터 무제한) 고정. EOL 도래 시 6.x 마이그레이션 또는 라이선스 발급으로 대응.

## Sub_goals 목록
| # | ID | service_name | 요약 |
|---|---|---|---|
| 1 | `emqx` | `emqx` | EMQX 5.8.6 StatefulSet 3 Pod, DNS 디스커버리 HA 클러스터 |
| 2 | `cilium-l2-vip` | `cilium-l2` | Cilium L2 Announcements VIP, 단일 LoadBalancer IP ARP 광고 |

---

## Sub_goal: `emqx`
- **service_name**: `emqx`

### 1. 목표 사양
- **기능**: EMQX 3-Pod StatefulSet을 `gikview` 네임스페이스에 배포. K3s DNS 기반 static cluster 디스커버리로 active-active HA 클러스터 구성. 각 Pod는 서로 다른 노드에 스케줄링(anti-affinity).
- **기술 스택**: EMQX 5.8.6, Helm chart `emqx/emqx` 5.8.6 (repo: `https://repos.emqx.io/charts`)
- **배포 경로**: `edge-server/helm/emqx/`
- **이미지**: Docker Hub 공개 (`docker.io/emqx/emqx:5.8.6`)

### 2. 인터페이스
- **Namespace**: `gikview`
- **Port**:
  - `mqtt: 1883` — 평문, 내부망 (mTLS 설정 전 검증용)
  - `mqtts: 8883` — mTLS, `security` 페이즈 `step-ca` 이후 활성화
  - `dashboard: 18083` — EMQX Dashboard API
  - `ekka: 4370` — EMQX 클러스터 내부 RPC
- **Labels**: `app.kubernetes.io/name: emqx`
- **dependency**: 없음
- **리소스**:
  - CPU: `200m` / `500m`
  - Memory: `384Mi` / `512Mi` (개발 alpha 클러스터)
- **EMQX DNS 디스커버리** — domain_suffix는 환경마다 다르므로 values 파일을 분리:
  ```yaml
  # values.yaml (공통)
  emqxConfig:
    EMQX_CLUSTER__DISCOVERY_STRATEGY: "dns"
    EMQX_CLUSTER__DNS__RECORD_TYPE: "a"

  # values-dev.yaml (domain_suffix: alpha.nexus.local)
  emqxConfig:
    EMQX_CLUSTER__DNS__NAME: "emqx-headless.gikview.svc.alpha.nexus.local"
    EMQX_NODE__NAME: "emqx@$(POD_NAME).emqx-headless.gikview.svc.alpha.nexus.local"

  # values-prod.yaml (domain_suffix: cluster.local)
  emqxConfig:
    EMQX_CLUSTER__DNS__NAME: "emqx-headless.gikview.svc.cluster.local"
    EMQX_NODE__NAME: "emqx@$(POD_NAME).emqx-headless.gikview.svc.cluster.local"
  ```
- **Anti-affinity**: `requiredDuringSchedulingIgnoredDuringExecution`, key `app.kubernetes.io/name: emqx`

### 3. 검증 명령어
```bash
# [check] pod_ready
kubectl wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=emqx \
  -n gikview --timeout=300s
# 기대: exit 0, 3 pod ready

# [check] cluster_status
kubectl exec -n gikview emqx-0 -- emqx ctl cluster status
# 기대: 출력에 running_nodes 3개 (emqx@emqx-0, emqx-1, emqx-2) 포함

# [check] no_warning_events
kubectl get events -n gikview \
  --field-selector involvedObject.name=emqx,type=Warning \
  --sort-by='.lastTimestamp'
# 기대: 최근 5분 내 출력 없음
```

### 4. Smoke Test
- **경로**: `edge-server/scripts/smoke-test-emqx.sh`
- **검증**:
  1. `emqx ctl cluster status` — running_nodes 3개 확인
  2. port-forward 경유 `mosquitto_pub/sub` — 1883 pub/sub 왕복 성공
  3. port-forward 경유 Dashboard API `/api/v5/nodes` — 3 노드 `running`

### 5. 제약사항
- `EMQX_NODE__NAME`을 Pod FQDN 형식으로 고정하지 않으면 재시작마다 mnesia 데이터 불일치 발생.
- anti-affinity를 `required`로 설정해 3 Pod가 반드시 다른 노드에 배치되어야 함.

---

## Sub_goal: `cilium-l2-vip`
- **service_name**: `cilium-l2`

### 1. 목표 사양
- **기능**: Cilium L2 Announcements를 통해 학내망에서 ARP 광고되는 단일 VIP(LoadBalancer IP)를 EMQX 앞단에 구성. 노드 장애 시 Cilium이 자동으로 다른 노드에서 ARP 광고 재개(lease 기반 VIP 마이그레이션).
- **기술 스택**: Cilium 1.19.2 (기배포), `CiliumLoadBalancerIPPool` CRD, `CiliumL2AnnouncementPolicy` CRD
- **배포 경로**: `edge-server/manifests/cilium-l2/`
- **이미지**: 해당 없음 (Cilium 기존 DaemonSet 활용)

### 2. 인터페이스
- **Namespace**: `gikview` (Service), `kube-system` (Cilium 설정)
- **Port**:
  - `mqtt: 1883` — LoadBalancer Service로 노출 (mTLS 활성화 전)
  - `mqtts: 8883` — LoadBalancer Service로 노출 (mTLS 활성화 후)
- **Labels**: `app.kubernetes.io/name: emqx` (emqx-lb Service selector)
- **dependency**: `emqx`
- **VIP 주소**: 학내망 서브넷 내 미사용 IP 1개 사전 확보 후 `CiliumLoadBalancerIPPool` spec에 명시
- **리소스**:
  - CPU: 해당 없음
  - Memory: 해당 없음

### 3. 검증 명령어
```bash
# [check] vip_assigned
kubectl get svc emqx-lb -n gikview -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
# 기대: 빈 값이 아닌 VIP IP 출력 (예: 192.168.0.200)

# [check] no_warning_events
kubectl get events -n gikview \
  --field-selector involvedObject.name=emqx-lb,type=Warning \
  --sort-by='.lastTimestamp'
# 기대: 최근 5분 내 출력 없음
```

### 4. Smoke Test
- **경로**: `edge-server/scripts/smoke-test-cilium-l2.sh`
- **검증**:
  1. `kubectl get svc emqx-lb` — `EXTERNAL-IP` 비어있지 않음
  2. `nc -zv <VIP> 1883` — exit 0

### 5. 제약사항
- `ExternalTrafficPolicy: Cluster` 필수. `Local` 사용 시 Cilium L2 Announcements known issue로 VIP 동작 불가.
- Cilium L2 Announcements는 L2 세그먼트(동일 스위치/VLAN)에서만 동작. 개발(alpha 클러스터)에서 ARP 광고 검증은 제한적이며, 기능 검증은 운영(RPi4, Cilium 1.19.2) 환경에서 수행.
- Cilium L2 Announcements 활성화(`l2announcements.enabled: true`, `externalIPs.enabled: true`)는 Cilium Helm 재배포 필요 — 하네스 범위 외, 사전 수동 적용.

---

## Phase 완료 기준
- 모든 Sub_goal의 Smoke Test 성공
- EMQX 3 Pod `running_nodes` 클러스터 상태 확인
- VIP ARP 광고 및 1883 연결 학내망 검증 완료
