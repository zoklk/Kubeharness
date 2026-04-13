# Phase: `visibility`

## Phase 개요
- **목적**: Prometheus가 클러스터 전체 메트릭을 수집하고, Grafana가 재실 현황과 인프라 상태를 시각화하며, eBPF TC hook이 EMQX MQTT 트래픽을 커널 레벨에서 계측하는 상태
- **관련 기술**: Prometheus, Grafana, Node Exporter, EMQX Exporter, Cilium Hubble, eBPF TC hook (직접 구현)

## Sub_goals 목록
| # | ID | service_name | 요약 |
|---|---|---|---|
| 1 | `prometheus` | `prometheus` | Prometheus 메트릭 수집 |
| 2 | `grafana` | `grafana` | Grafana 시각화 + Alertmanager 연동 |
| 3 | `node-exporter` | `node-exporter` | RPi 시스템 메트릭 DaemonSet |
| 4 | `emqx-exporter` | `emqx-exporter` | MQTT 브로커 메트릭 Prometheus 노출 |
| 5 | `ebpf-tc-hook` | `ebpf-tc-hook` | EMQX 트래픽 커널 레벨 계측, Prometheus export |

---

## Sub_goal: `prometheus`
- **service_name**: `prometheus`

### 1. 목표 사양
- **기능**:
- **기술 스택**:
- **배포 경로**: `edge-server/helm/prometheus/`
- **이미지**: Docker Hub 공개 ()

### 2. 인터페이스
- **Namespace**: `{NAMESPACE}`
- **Port**:
- **Labels**: `app.kubernetes.io/name: prometheus`
- **dependency**: 없음
- **리소스**:
  - CPU:
  - Memory:

### 3. Smoke Test
- **경로**: `edge-server/tests/visibility/smoke-test-prometheus.sh`
- **검증**:
  1.

---

## Sub_goal: `grafana`
- **service_name**: `grafana`

### 1. 목표 사양
- **기능**:
- **기술 스택**:
- **배포 경로**: `edge-server/helm/grafana/`
- **이미지**: Docker Hub 공개 ()

### 2. 인터페이스
- **Namespace**: `{NAMESPACE}`
- **Port**:
- **Labels**: `app.kubernetes.io/name: grafana`
- **dependency**: `prometheus`
- **리소스**:
  - CPU:
  - Memory:

### 3. Smoke Test
- **경로**: `edge-server/tests/visibility/smoke-test-grafana.sh`
- **검증**:
  1.

---

## Sub_goal: `node-exporter`
- **service_name**: `node-exporter`

### 1. 목표 사양
- **기능**:
- **기술 스택**:
- **배포 경로**: `edge-server/helm/node-exporter/`
- **이미지**: Docker Hub 공개 ()

### 2. 인터페이스
- **Namespace**: `{NAMESPACE}`
- **Port**:
- **Labels**: `app.kubernetes.io/name: node-exporter`
- **dependency**: `prometheus`
- **리소스**:
  - CPU:
  - Memory:

### 3. Smoke Test
- **경로**: `edge-server/tests/visibility/smoke-test-node-exporter.sh`
- **검증**:
  1.

---

## Sub_goal: `emqx-exporter`
- **service_name**: `emqx-exporter`

### 1. 목표 사양
- **기능**:
- **기술 스택**:
- **배포 경로**: `edge-server/helm/emqx-exporter/`
- **이미지**: Docker Hub 공개 ()

### 2. 인터페이스
- **Namespace**: `{NAMESPACE}`
- **Port**:
- **Labels**: `app.kubernetes.io/name: emqx-exporter`
- **dependency**: `emqx`, `prometheus`
- **리소스**:
  - CPU:
  - Memory:

### 3. Smoke Test
- **경로**: `edge-server/tests/visibility/smoke-test-emqx-exporter.sh`
- **검증**:
  1.

---

## Sub_goal: `ebpf-tc-hook`
- **service_name**: `ebpf-tc-hook`

### 1. 목표 사양
- **기능**:
- **기술 스택**: eBPF TC hook (직접 구현), Prometheus exporter
- **배포 경로**: `edge-server/helm/ebpf-tc-hook/`
- **이미지**: 커스텀 빌드 (`ghcr.io/<org>/ebpf-tc-hook:<tag>`)

### 2. 인터페이스
- **Namespace**: `{NAMESPACE}`
- **Port**:
- **Labels**: `app.kubernetes.io/name: ebpf-tc-hook`
- **dependency**: `emqx`, `prometheus`
- **리소스**:
  - CPU:
  - Memory:

### 3. Smoke Test
- **경로**: `edge-server/tests/visibility/smoke-test-ebpf-tc-hook.sh`
- **검증**:
  1.

---

## Phase 완료 기준
- 모든 Sub_goal의 Smoke Test 성공
