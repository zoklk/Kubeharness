# Tech Stack

> 사람 + 모델용. Developer / Runtime Verifier 프롬프트에 첨부됨.
> **변경 시 이 파일만 수정하면 LLM이 자동으로 최신 정보 참조**.

## 대상 클러스터

### 개발 (연구실 클러스터)
- Kubernetes: 1.34.3
- CNI: Cilium 1.18.6 (kubeProxyReplacement=true)
- 노드: `alpha-m1`(cp), `alpha-w1/w2/w3`(worker)
- 네임스페이스: `gikview`

### 운영 (엣지, 본 하네스 범위 밖)
- 노드: `e-s1/e-s2/e-s3` (RPi4 4GB, Ubuntu Server 24.04 arm64)
- K3s HA (임베디드 etcd, 3 Server)
- CNI: Cilium 1.19.2 (kubeProxyReplacement=true)
- 배포: argocd 사람 수동 트리거

## 컴포넌트와 버전

### messaging
- **EMQX** 5.8.6 (arm64 공식 이미지). MQTT broker, HA 클러스터 (StatefulSet 3 Pod, active-active)
- **Cilium L2 Announcements** (Cilium 1.19.2 내장, Beta). EMQX 앞단 단일 VIP 진입점.
  - `CiliumLoadBalancerIPPool`: VIP 대역 정의
  - `CiliumL2AnnouncementPolicy`: 광고 대상 Service 및 노드 선언
  - `ExternalTrafficPolicy: Cluster` 필수 (Local은 known issue)

### pipeline
- **Edge Gateway Pod** (자체 코드, K3s Deployment, replica=3, podAntiAffinity hard).
  - K8s Lease(`coordination.k8s.io/v1`) 기반 leader election
  - leader만 EMQX 구독 + 인메모리 상태 맵 유지 + Lambda HTTPS POST + InfluxDB 쓰기
  - follower는 lease 갱신 참여만 수행
  - leader 교체 시 InfluxDB에서 room별 최신 상태 로드 후 구독 시작
- **ESP32Device CRD + kopf Operator** (자체 코드). 디바이스 등록/상태 추적/설정 관리 (targetBSSID, 폴링 인터벌 등 런타임 변경).
- **Heartbeat Pod** (자체 코드, K3s CronJob). 10분 주기 Lambda heartbeat 엔드포인트 POST.

### visibility
- **Prometheus**: 메트릭 수집
- **Grafana**: 시각화 + Alertmanager 연동
- **Node Exporter** (DaemonSet)
- **EMQX Exporter**: MQTT 브로커 메트릭
- **Cilium Hubble**: Pod 간 트래픽 흐름
- **eBPF TC hook** (직접 구현): EMQX 트래픽 커널 레벨 계측

### storage
- **InfluxDB** 3.x: pod node selector로 관리, software-defined storage를 쓰기엔 micro sd 수명 이슈 (RPi #3 고정)

### security
- **step-ca**: Intermediate CA, EST 엔드포인트
- **mTLS** (MQTT 포트 8883)

### CI/CD / GitOps
- **로컬 하네스** (CI 주체): 정적 검증 → 런타임 검증 → LLM 수정 루프 → 이미지 빌드(linux/arm64) → GHCR Push → GitHub Push
- **GitHub Actions** (CI 보조): lint/format 체크만. 이미지 빌드 없음.
- **Argo CD** (RPi #1, non-HA): GitOps CD, 수동 트리거

## 검증 도구 (하네스가 사용)

- `yamllint`, `kubeconform`, `helm lint`
- `trivy config` (config 스캔)
- `gitleaks` (secret 누출)
- `kubectl/helm --dry-run=server` (immutable 충돌 사전 감지)
- `hadolint` (Dockerfile 정적 검사, 미설치 시 skip)

## LLM

- 개발 시작 시: gemma-4-31b-it (API key)
- 이후: 연구실 망 gemma-4-26B-A4B-it
- 교체는 `config/llm.yaml` 한 줄 변경ㅈ