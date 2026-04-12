# Cilium Knowledge

> 이 문서는 Developer 노드에게 고신뢰 레퍼런스로 주입됩니다.
> 모든 설정값은 이 환경(GikView)에서 검증된 것이어야 합니다.

## 이미지 / 버전

**채택**: `v1.19.2` (기배포된 Cilium 에이전트 버전 활용)

Cilium L2 Announcements 기능은 Beta 상태이며, `l2announcements.enabled: true` 및 `externalIPs.enabled: true` 설정이 사전 적용되어 있어야 함.

## 주요 설정

학내망(L2 세그먼트) 내에서 ARP 광고를 통한 단일 VIP(LoadBalancer)를 구성하기 위해 다음 CRD 설정이 필수임.

```yaml
# CiliumLoadBalancerIPPool 예시
apiVersion: cilium.io/v2alpha1
kind: CiliumLoadBalancerIPPool
metadata:
  name: <pool-name>
spec:
  blocks:
    - cidr: "<VIP_IP>/32" # 학내망에서 확보한 미사용 IP

# CiliumL2AnnouncementPolicy 예시
apiVersion: cilium.io/v2alpha1
kind: CiliumL2AnnouncementPolicy
metadata:
  name: <policy-name>
spec:
  serviceSelector:
    matchLabels:
      app.kubernetes.io/name: emqx
  nodeSelector:
    matchLabels:
      kubernetes.io/hostname: <target-node> # 또는 전체 노드
  interfaces:
    - ^eth0$ # 실제 ARP 광고를 수행할 인터페이스 이름
  externalIPs: true
  loadBalancerIPs: true
```

## 알려진 주의사항

- **Traffic Policy 제약**: Cilium L2 Announcements는 `ExternalTrafficPolicy: Local` 사용 시 알려진 이슈로 인해 VIP 동작이 불가함. 반드시 `ExternalTrafficPolicy: Cluster`를 사용해야 함.
- **L2 세그먼트**: ARP 광고는 동일 스위치/VLAN(L2) 내에서만 동작함. 라우터를 넘어서는 환경에서는 BGP 등의 다른 전략이 필요함.
- **Lease 기반 마이그레이션**: 노드 장애 시 Cilium이 자동으로 다른 노드에서 ARP 광고를 재개하므로 서비스 중단 시간을 최소화할 수 있음.

## 환경별 분리 필요 항목

| 항목 | dev (alpha cluster) | prod (edge) |
| :--- | :--- | :--- |
| `VIP_IP` | `192.168.0.200` | `10.0.x.x` (실제 할당 IP) |
| `interfaces` | `^eth0$` | `^eth0$` (HW 인터페이스 확인 필요) |