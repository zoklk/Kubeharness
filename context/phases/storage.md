# Phase: `storage`

## Phase 개요
- **목적**: InfluxDB 3.x가 `node_storage` 노드에 고정 배포되어 Edge Gateway의 재실 시계열 데이터를 무제한 보존하는 상태
- **관련 기술**: InfluxDB 3.x

## Sub_goals 목록
| # | ID | service_name | 요약 |
|---|---|---|---|
| 1 | `influxdb` | `influxdb` | InfluxDB 3.x, node_storage 노드 고정, hostPath local PV |

---

## Sub_goal: `influxdb`
- **service_name**: `influxdb`

### 1. 목표 사양
- **기능**: Edge Gateway가 재실 상태 시계열을 쓰고, 리더 재선출 시 room별 최신 상태를 읽어 복원하는 InfluxDB 3.x 인스턴스. Retention 무제한. `config/cluster.yaml`의 `node_storage` 노드에 고정 배포.
- **기술 스택**: InfluxDB 3.x, Helm chart `influxdata/influxdb3` (repo: `https://helm.influxdata.com/`)
- **배포 경로**: `edge-server/helm/influxdb/`
- **이미지**: Docker Hub 공개 (`influxdb:3`)

### 2. 인터페이스
- **Namespace**: `gikview`
- **Port**:
  - `http: 8086` — HTTP API (write / query)
- **Labels**: `app.kubernetes.io/name: influxdb`
- **dependency**: 없음
- **nodeSelector**: `kubernetes.io/hostname` 기준. `values-dev.yaml`에 dev(`alpha-w3`), `values-prod.yaml`에 prod(`e-s3`) 각각 명시
- **리소스**:
  - CPU: `200m` / `500m`
  - Memory: `256Mi` / `512Mi`

### 3. 검증 명령어
```bash
# [check] pod_ready
kubectl wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=influxdb \
  -n gikview --timeout=300s
# 기대: exit 0

# [check] health
kubectl exec -n gikview deploy/influxdb -- \
  curl -sf http://localhost:8086/health
# 기대: exit 0, 출력에 "pass" 포함
```

### 4. Smoke Test
- **경로**: `edge-server/tests/storage/smoke-test-influxdb.sh`
- **검증**:
  1. port-forward 경유 `/health` — HTTP 200, `"pass"` 포함
  2. line protocol write (`/api/v2/write`) — HTTP 204
  3. query로 write한 데이터 확인 — 결과에 `smoke` 포함

### 5. 제약사항
- `nodeSelector: kubernetes.io/hostname: <node_storage>` 고정 필수. 다른 노드 스케줄링 시 hostPath PV 마운트 실패.
- `values.yaml`에 dev 노드(`alpha-w3`), `values-prod.yaml`에 prod 노드(`e-s3`)를 각각 `nodeSelector`로 명시. 하네스가 활성 환경에 맞는 values 파일을 자동 선택.

---

## Phase 완료 기준
- 모든 Sub_goal의 Smoke Test 성공
