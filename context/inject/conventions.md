# Conventions

> 사람 + 모델용. Developer / Runtime Verifier 프롬프트에 첨부됨.
> 이 컨벤션이 깨지면 Runtime Verifier 결정적 게이트가 동작하지 않는다.

## 네임스페이스

- **개발**: `gikview` 고정. 모든 리소스가 여기 배포됨
- 다른 네임스페이스 사용 금지

## 파일 경로 (Developer가 쓸 수 있는 경로)

- **기본 배포 수단은 Helm**. 거의 모든 서비스는 `edge-server/helm/<service>/` 하위 차트로 관리
- `edge-server/manifests/<service>/`: 단일 리소스 YAML. 예외적 경우만
- `edge-server/scripts/smoke-test-<service>.sh`: 사람이 미리 작성
- `edge-server/docker/<service>/`: 커스텀 이미지 빌드 컨텍스트
- `edge-server/ebpf/<module>/`: eBPF 소스코드 작성 위치 (빌드 및 연결은 사람이 직접)

**Developer는 `edge-server/` 외부 경로 작성 절대 금지**. `harness/`, `context/`, `config/`, `tests/`, `scripts/` 등은 hands off.

## Helm 차트 구조

```
edge-server/helm/<service>/
├── Chart.yaml
├── values.yaml         # 공통 기본값 (환경 중립)
├── values-dev.yaml     # 개발 환경 오버라이드 (연구실 클러스터)
├── values-prod.yaml    # 운영 환경 오버라이드 (RPi4 엣지 클러스터)
└── templates/
    ├── _helpers.tpl
    ├── deployment.yaml
    ├── service.yaml
    ├── configmap.yaml
    └── secret.yaml
```

### values.yaml / values-dev.yaml / values-prod.yaml 작성 규칙

**반드시 세 파일 모두 작성한다.** values-dev.yaml과 values-prod.yaml은 절대 생략하지 않는다.

`values.yaml`은 환경 중립적 기본값만 담는다. 환경별 차이는 반드시 오버라이드 파일에서 처리한다.

> **각 환경의 실제 domain_suffix와 arch 값은 `## Cluster Environments` 섹션을 참조.**
> conventions.md는 규칙만 기술하고 구체적 값은 기술하지 않는다.

| 항목 | values.yaml | values-dev.yaml | values-prod.yaml |
|------|-------------|-----------------|-----------------|
| 도메인 suffix | (미정의) | dev 환경 domain_suffix | prod 환경 domain_suffix |
| 이미지 arch | (미정의 또는 기본) | dev 환경 arch | prod 환경 arch |
| 리소스 limits | 기본값 기준 | 개발용 (여유) | 운영용 (엄격) |

환경별 DNS 이름이 필요한 경우 (클러스터 디스커버리, headless service FQDN 등):
- 각 환경의 `domain_suffix` 값으로 `svc.<domain_suffix>` suffix 사용

아키텍처 차이 처리 방법:
- **멀티 아키 이미지** (대부분의 공식 이미지): 별도 처리 불필요. K8s가 노드 아키텍처에 맞는 이미지를 자동 선택.
- **단일 아키 이미지** (아키별 태그가 다른 경우): values-dev.yaml, values-prod.yaml에서 `image.tag`를 아키별 태그로 오버라이드.
- **혼합 아키 클러스터에서 특정 노드 고정이 필요한 경우**: `nodeSelector` 사용.

```yaml
# values-dev.yaml (단일 아키 이미지 예시)
image:
  tag: "5.8.6-amd64"   # dev 환경 arch에 맞는 태그

# values-dev.yaml (nodeSelector 예시, 혼합 아키 클러스터)
nodeSelector:
  kubernetes.io/arch: amd64
```

## 릴리즈 이름

- 형식: `<service>-dev-v1`
- `dev`는 개발 환경 식별자. 네임스페이스(`gikview`)와 구분되는 개념
- 예: `emqx-dev-v1`, `prometheus-dev-v1`

## 배포 명령 (Runtime Verifier가 자동 도출)

service 이름만 알면 아래 명령 전체가 자동 생성됨. sub_goal 이름이 곧 service 이름.

### 기본 (Docker Hub 등 공개 이미지)

```bash
helm upgrade --install <service>-dev-v1 edge-server/helm/<service> \
  -n gikview \
  -f edge-server/helm/<service>/values.yaml \
  -f edge-server/helm/<service>/values-dev.yaml
```

### 커스텀 이미지 서비스 (`edge-server/docker/<service>/Dockerfile` 존재 시)

Runtime Verifier가 helm install **이전에** 자동으로 빌드·푸시 수행:

```bash
# ① 이미지 빌드 & 푸시 (config/build.yaml에서 registry, image_tag 읽음)
docker build -t <registry>/<service>:<image_tag> edge-server/docker/<service>/
docker push <registry>/<service>:<image_tag>

# ② helm install (위와 동일)
helm upgrade --install <service>-dev-v1 edge-server/helm/<service> \
  -n gikview \
  -f edge-server/helm/<service>/values.yaml \
  -f edge-server/helm/<service>/values-dev.yaml
```

Dockerfile이 없는 서비스는 빌드 스텝이 자동으로 skip됨.

## 필수 라벨 (모든 리소스)

Kubernetes recommended labels 준수:

```yaml
metadata:
  labels:
    app.kubernetes.io/name: <service>           # 예: emqx
    app.kubernetes.io/instance: <release-name>  # 예: emqx-dev-v1
    app.kubernetes.io/version: "<semver>"
    app.kubernetes.io/component: <role>         # broker, exporter 등
    app.kubernetes.io/part-of: <group>          # messaging, monitoring 등
    app.kubernetes.io/managed-by: harness
    stage: dev                                   # dev | prod
```

- Runtime Verifier가 `kubectl wait -l app.kubernetes.io/name=<service>`로 pod 대기
- `managed-by=harness`로 하네스 관리 리소스만 스코프 조회 가능

## 어노테이션

```yaml
metadata:
  annotations:
    harness.local/sub-goal: <sub_goal name>
    harness.local/phase: <phase name>
    harness.local/created-at: <ISO8601>
```

## 리소스 limits/requests

### values-dev.yaml (연구실 클러스터, 여유롭게)
- request 256Mi / limit 1Gi, CPU 100m / 1000m 기본

### values-prod.yaml (rpi4 4GB, 엄격)
| 유형 | req mem | lim mem | req cpu | lim cpu |
|---|---|---|---|---|
| 경량 (exporter) | 32Mi | 128Mi | 50m | 300m |
| 중간 (grafana) | 128Mi | 256Mi | 100m | 500m |
| 메인 (emqx, prometheus) | 256Mi | 512Mi | 200m | 1000m |

## Probe

- 모든 Deployment에 `readinessProbe`, `livenessProbe` 설정
- timeout/period는 기술별 공식 문서 기준

## 이미지

- **`latest` 태그 금지**. 명시적 semver만
- `values.yaml`에 tag를 변수로 분리
- Secret(레지스트리 credential 등) 평문 금지. 외부 주입 전제

### 커스텀 이미지 (ghcr.io)

`edge-server/docker/<service>/Dockerfile`을 작성하는 서비스는 이미지 참조를 다음 형식으로 고정:

```yaml
# values.yaml
image:
  repository: ghcr.io/<org>/<service>   # config/build.yaml의 registry 값과 일치
  tag: dev                               # config/build.yaml의 image_tag 값과 일치
  pullPolicy: Always
```

- `<org>`와 `tag`는 `config/build.yaml`에 정의된 값을 그대로 사용
- Runtime Verifier가 build→push 후 이 태그로 helm install하므로 반드시 일치해야 함

## ConfigMap / Secret 이름

- 형식: `<service>-<purpose>-<version>`
- 예: `emqx-config-v1`, `prometheus-rules-v2`

## Smoke Test

- 위치: `edge-server/scripts/smoke-test-<service>.sh`
- **사람이 미리 작성**. Developer/Tester가 자동 생성하지 않음
- 없으면 Runtime Verifier가 smoke test 단계를 skip
- exit 0이면 pass, 그 외 fail

## 멱등성

- 재배포는 `helm upgrade --install` 한 번으로 끝
- delete + recreate 금지
- immutable 필드 충돌은 Static Verifier의 `helm --dry-run=server`에서 사전 감지 목표