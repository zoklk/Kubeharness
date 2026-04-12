# kagent MCP Tools Reference

> 사람용 reference. LLM 프롬프트에는 첨부하지 않음.
> 실제 화이트리스트는 `config/kagent.yaml`에 있음. 이 문서는 정책 판단 기록.
> 출처: https://kagent.dev/tools/

## 정책

- Developer, Runtime Verifier Phase 2: **read-only tool만**
- 쓰기 작업은 모두 결정적 게이트 (subprocess `helm` / `kubectl`)에서 처리
- 화이트리스트 방식 — yaml에 등록된 tool만 LLM에 노출

## Kubernetes

### 사용 (read-only)
| Tool | 설명 |
|---|---|
| GetResources | k8s 리소스 조회 |
| GetResourceYAML | 리소스 YAML 표현 |
| DescribeResource | 상세 정보 |
| GetEvents | 이벤트 |
| GetPodLogs | pod 로그 |
| CheckServiceConnectivity | service 연결성 |
| GetAvailableAPIResources | 지원 API 리소스 |
| GetClusterConfiguration | 클러스터 설정 |

### 금지
| Tool | 사유 |
|---|---|
| ApplyManifest | 쓰기. 결정적 게이트에서 처리 |
| CreateResource, CreateResourceFromUrl | 쓰기. URL은 외부 의존성 위험 |
| DeleteResource | 자동 삭제 금지 |
| PatchResource | 쓰기 |
| AnnotateResource, RemoveAnnotation | 쓰기 |
| LabelResource, RemoveLabel | 쓰기 |
| Rollout | 쓰기 |
| Scale | 쓰기 |
| ExecuteCommand | pod 내 임의 명령. 읽기/쓰기 구분 불가 |
| GenerateResourceTool | Developer LLM과 역할 중복 |

## Helm

### 사용 (read-only)
| Tool | 설명 |
|---|---|
| GetRelease | release 정보 |
| ListReleases | release 목록 |

### 금지
| Tool | 사유 |
|---|---|
| Upgrade | 쓰기. 결정적 게이트에서 직접 호출 |
| Uninstall | 자동 삭제 금지 |
| RepoAdd, RepoUpdate | 사람이 사전 설정 |

## Cilium

프로젝트에서 cilium 관련 sub_goal 생길 때 참고.
전체 목록: https://kagent.dev/tools/cilium
필요 시 read-only tool을 골라 `config/kagent.yaml`과 이 문서에 추가.

## 새 tool 추가 절차

1. https://kagent.dev/tools 에서 확인
2. read-only 여부 판단
3. 이 문서 업데이트
4. `config/kagent.yaml`의 `developer_tools` / `runtime_verifier_tools`에 추가
5. 다음 invocation부터 사용 가능