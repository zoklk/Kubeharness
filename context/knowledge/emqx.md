# EMQX Knowledge

> 이 문서는 Developer 노드에게 고신뢰 레퍼런스로 주입됩니다.
> 모든 설정값은 이 환경(GikView)에서 검증된 것이어야 합니다.

## 이미지 / 버전

**채택**: `emqx:5.8.6` (`docker.io/emqx/emqx:5.8.6`)

v5.9.0부터 BSL 1.1로 라이선스가 변경되어 1노드 초과 클러스터 구성 시 라이선스 키가 필수임. 5.8.6 버전은 Apache 2.0 라이선스로 제한 없는 클러스터 구성이 가능한 마지막 버전임.

Helm chart: `emqx/emqx` `5.8.6` (repo: `https://repos.emqx.io/charts`)

## 주요 설정

K3s 내부 DNS를 통한 정적 클러스터 디스커버리를 사용함. `RECORD_TYPE`과 `NODE__NAME`의 형식이 일치하지 않으면 `integrity_validation_failure`로 인해 포드가 기동되지 않음.

```yaml
# values.yaml (공통)
emqxConfig:
  EMQX_CLUSTER__DISCOVERY_STRATEGY: "dns"
  EMQX_CLUSTER__DNS__RECORD_TYPE: "srv"  # FQDN 기반 디스커버리 필수

# securityContext 및 볼륨 설정 (Trivy 보안 가이드 준수)
containerSecurityContext:
  readOnlyRootFilesystem: true

extraVolumeMounts:
  - name: emqx-data
    mountPath: /opt/emqx/data
  - name: emqx-log
    mountPath: /opt/emqx/log
  - name: tmp
    mountPath: /tmp
```

## 알려진 주의사항

- **노드 네이밍 규칙**: `EMQX_CLUSTER__DNS__RECORD_TYPE`이 `"srv"`일 경우, `EMQX_NODE__NAME`은 반드시 FQDN 형식(`emqx@<pod>.<svc>.<ns>.svc.<domain>`)이어야 함.
- **데이터 일관성**: IP 기반 노드 네이밍은 포드 재시작 시 Mnesia 데이터 불일치를 유발함. 반드시 고정된 FQDN 형식을 사용하여 노드 식별자를 유지해야 함.
- **Headless Service 필수 구성**: SRV 레코드 생성을 위해 서비스의 `ports` 섹션에 `ekka` 포트(4370)의 `name`이 반드시 명시되어야 함.
- **권한 제약**: `readOnlyRootFilesystem` 적용 시 데이터 및 로그 경로에 쓰기 가능한 볼륨(EmptyDir 등) 할당이 누락되면 기동에 실패함.

## 환경별 분리 필요 항목

| 항목 | dev (alpha cluster) | prod (edge) |
|------|-----|------|
| `EMQX_CLUSTER__DNS__NAME` | `emqx-headless.gikview.svc.alpha.nexus.local` | `emqx-headless.gikview.svc.cluster.local` |
| `EMQX_NODE__NAME` | `emqx@$(POD_NAME).emqx-headless.gikview.svc.alpha.nexus.local` | `emqx@$(POD_NAME).emqx-headless.gikview.svc.cluster.local` |
| `resources.requests.memory` | `384Mi` | `512Mi` |
| `resources.limits.memory` | `512Mi` | `1Gi` |