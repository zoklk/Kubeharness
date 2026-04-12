# EMQX Knowledge

## 이미지 버전

**채택: `emqx/emqx:5.8.6`** (`docker.io/emqx/emqx:5.8.6`)

v5.9.0부터 BSL 1.1로 라이선스 변경. 1노드 초과 클러스터에 라이선스 키 필수.
5.8.6(Apache 2.0, 클러스터 무제한) 고정. EOL 도래 시 6.x 마이그레이션 또는 라이선스 발급으로 대응.

Helm chart: `emqx/emqx` 5.8.6 (repo: `https://repos.emqx.io/charts`)

## DNS 디스커버리 설정

EMQX는 headless Service의 SRV DNS 레코드로 클러스터 멤버를 자동 검색함.
domain_suffix는 환경마다 다르므로 values 파일을 분리해야 함.

```yaml
# values.yaml (공통)
emqxConfig:
  EMQX_CLUSTER__DISCOVERY_STRATEGY: "dns"
  EMQX_CLUSTER__DNS__RECORD_TYPE: "srv"

# values-dev.yaml (domain_suffix: alpha.nexus.local)
emqxConfig:
  EMQX_CLUSTER__DNS__NAME: "emqx-headless.gikview.svc.alpha.nexus.local"
  EMQX_NODE__NAME: "emqx@$(POD_NAME).emqx-headless.gikview.svc.alpha.nexus.local"

# values-prod.yaml (domain_suffix: cluster.local)
emqxConfig:
  EMQX_CLUSTER__DNS__NAME: "emqx-headless.gikview.svc.cluster.local"
  EMQX_NODE__NAME: "emqx@$(POD_NAME).emqx-headless.gikview.svc.cluster.local"
```

`EMQX_NODE__NAME`은 반드시 downward API (`$(POD_NAME)`)로 주입해야 함.
shell 확장(`${POD_NAME}`)은 values.yaml에서 동작하지 않음.

## mnesia 데이터 일관성

`EMQX_NODE__NAME`을 Pod FQDN 형식으로 고정하지 않으면 재시작마다 mnesia 데이터베이스 불일치 발생.
노드 이름이 변경되면 mnesia는 새 노드로 인식해 기존 데이터와 분리됨.
Pod FQDN = `emqx@<pod-name>.<headless-svc>.<namespace>.svc.<domain_suffix>`

## StatefulSet 주의사항

- headless Service (`clusterIP: None`) 필수 — SRV 레코드 생성 조건
- `publishNotReadyAddresses: true` — 초기 클러스터 구성 시 미준비 Pod도 DNS에 등록
- podManagementPolicy: `Parallel` 권장 — 3 Pod 동시 기동으로 클러스터 구성 시간 단축
