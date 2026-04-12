# Phase: `pipeline`

## Phase 개요
- **목적**: Edge Gateway가 EMQX를 구독해 재실 상태를 판단하고 InfluxDB에 저장하며 leader election으로 단일 처리자가 보장되고, ESP32Device CRD Operator가 디바이스 등록·상태를 관리하는 상태
- **관련 기술**: Edge Gateway (자체 코드, Python/Go), ESP32Device CRD + kopf Operator (자체 코드, Python)

## Sub_goals 목록
| # | ID | service_name | 요약 |
|---|---|---|---|
| 1 | `edge-gateway` | `edge-gateway` | 재실 상태 판단 + InfluxDB 쓰기, K8s Lease leader election, replica=3 |
| 2 | `esp32device-operator` | `esp32device-operator` | ESP32Device CRD + kopf Operator, 디바이스 등록/상태 추적 |

---

## Sub_goal: `edge-gateway`
- **service_name**: `edge-gateway`

### 1. 목표 사양
- **기능**: EMQX를 구독해 room별 재실 상태를 판단하고 InfluxDB에 시계열로 기록. replica=3, podAntiAffinity hard로 노드당 1개 배치. K8s Lease(`coordination.k8s.io/v1`) leader election으로 leader 1개만 EMQX 구독·상태 처리, follower는 lease 갱신만 수행. leader 교체 시 InfluxDB에서 room별 최신 상태 로드 후 구독 재시작.
- **기술 스택**: 자체 코드, K8s Deployment, K8s Lease
- **소스코드 경로**: `edge-server/docker/edge-gateway/` (Dockerfile + 애플리케이션 소스 — Developer 작성 대상)
- **배포 경로**: `edge-server/helm/edge-gateway/` (Helm chart)
- **이미지**: 커스텀 빌드 (`ghcr.io/<org>/edge-gateway:<tag>`, linux/amd64,linux/arm64) — 하네스가 빌드·푸시, Developer는 소스코드와 Dockerfile만 작성

### 2. 인터페이스
- **Namespace**: `gikview`
- **Port**: 없음 (외부 노출 불필요)
- **Labels**: `app.kubernetes.io/name: edge-gateway`
- **dependency**: `emqx`, `influxdb`
- **Anti-affinity**: `requiredDuringSchedulingIgnoredDuringExecution`, key `app.kubernetes.io/name: edge-gateway`
- **리소스**:
  - CPU: `100m` / `300m`
  - Memory: `128Mi` / `256Mi`

### 3. 검증 명령어
```bash
# [check] pod_ready
kubectl wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=edge-gateway \
  -n gikview --timeout=300s
# 기대: exit 0, 3 pod ready

# [check] lease_holder
kubectl get lease edge-gateway-leader -n gikview \
  -o jsonpath='{.spec.holderIdentity}'
# 기대: 비어있지 않은 값 (pod 이름)
```

### 4. Smoke Test
- **경로**: `edge-server/tests/pipeline/smoke-test-edge-gateway.sh`
- **검증**:
  1. 3 pod Ready 확인
  2. Lease holder 존재 확인

---

## Sub_goal: `esp32device-operator`
- **service_name**: `esp32device-operator`

### 1. 목표 사양
- **기능**: ESP32Device CRD를 정의하고 kopf 기반 Operator가 디바이스 등록·연결 상태·설정(targetBSSID, 폴링 인터벌)을 K8s 오브젝트로 선언적 관리. 디바이스 이상 감지 시 Alertmanager 연동.
- **기술 스택**: 자체 코드, kopf (Python), K8s CRD (`ESP32Device`)
- **소스코드 경로**: `edge-server/docker/esp32device-operator/` (Dockerfile + 애플리케이션 소스 — Developer 작성 대상)
- **배포 경로**: `edge-server/helm/esp32device-operator/` (Helm chart)
- **이미지**: 커스텀 빌드 (`ghcr.io/<org>/esp32device-operator:<tag>`, linux/amd64,linux/arm64) — 하네스가 빌드·푸시, Developer는 소스코드와 Dockerfile만 작성

### 2. 인터페이스
- **Namespace**: `gikview`
- **Port**: 없음
- **Labels**: `app.kubernetes.io/name: esp32device-operator`
- **dependency**: 없음
- **nodeAffinity**: `preferredDuringSchedulingIgnoredDuringExecution` (장애 시 재스케줄링 허용)
- **리소스**:
  - CPU: `50m` / `200m`
  - Memory: `64Mi` / `128Mi`

### 3. 검증 명령어
```bash
# [check] pod_ready
kubectl wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=esp32device-operator \
  -n gikview --timeout=300s
# 기대: exit 0

# [check] crd_exists
kubectl get crd esp32devices.gikview.io
# 기대: exit 0
```

### 4. Smoke Test
- **경로**: `edge-server/tests/pipeline/smoke-test-esp32device-operator.sh`
- **검증**:
  1. CRD 등록 확인
  2. 테스트 CR 생성 후 operator가 status 갱신 확인

---

## Phase 완료 기준
- 모든 Sub_goal의 Smoke Test 성공
- leader Pod의 InfluxDB 쓰기 정상 확인
