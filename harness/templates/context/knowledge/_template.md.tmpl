# <Technology> Knowledge

> 이 문서는 `{{workspace_dir}}/helm/<service>/` · `{{workspace_dir}}/docker/<service>/`
> 아티팩트를 작성하는 LLM 에 주입됩니다. 모든 설정값은 이 프로젝트에서
> 실제로 검증된 것이어야 합니다. `context/phases/<phase>.md` 의 sub_goal
> 이 `**references**:` 로 이 파일을 가리키면 phase-spec-reader 가
> 자동으로 Read 합니다. runtime-diagnoser 도 실패 진단 시 이 파일을
> 먼저 참조합니다.

이 파일을 `<technology>.md` (예: `emqx.md`, `prometheus.md`) 로
복사한 뒤 해당 기술 맥락에 맞게 채우세요. 복사하지 않은 원본
`_template.md` 는 참조 대상이 아닙니다. 해당 기술에 **정말로
해당 사항이 없는 섹션은 삭제**하세요 — 빈 섹션은 "정보 없음"
이 아니라 "아직 안 채웠음" 으로 오해됩니다.

## 이미지 / 버전

**채택**: `<registry>/<image>:<tag>` (예: `docker.io/emqx/emqx:5.8.6`)

이 특정 버전·태그를 선택한 이유를 한 단락으로 기록합니다 — 라이선스
변경, API breaking change, 보안 CVE 노출, 내부 종속성 호환 등. "최신"
을 쓰지 않는 경우에는 **왜** 를 반드시 남기세요. 향후 업그레이드
판단의 근거가 됩니다.

<Helm chart 를 사용하는 경우:>
Helm chart: `<chart-name>` `<version>` (repo: `<repo-url>`)

## 주요 설정

이 기술을 **정상 동작시키기 위해 반드시 필요한** 비자명 설정을 코드
블록 + 주석으로 기록합니다. 차트 기본값만으론 안 뜨거나, 뜨더라도
한두 번 재시작 후 깨지는 값들 — 즉 "다음 담당자가 값만 보고선 왜
그렇게 됐는지 추측할 수 없는" 설정이 여기 대상입니다.

```yaml
# values.yaml (공통)
<config-group>:
  <key>: "<value>"   # <왜 이 값이 필요한가 한 줄>
  <key>: "<value>"   # <다른 값과의 상호 의존성이 있다면 명시>
```

여러 블록이 필요하면 `# values.yaml`, `# values-prod.yaml` 등으로
소제목을 나눠 분리합니다. 긴 설정은 파일 자체에 주석을 박지 말고
여기에 주석으로 남기세요 — 차트 values 는 깨끗하게 유지.

## 알려진 주의사항

이 기술을 운영하며 실제로 겪거나 공식 문서에 명시된 함정. 각 bullet
은 **증상 → 원인 → 해결** 순서로 써서 runtime-diagnoser 가 실패 로그
를 보고 매칭할 수 있게 합니다.

- **<증상 또는 에러 메시지 키워드>**: <원인 한 줄>. <해결 / 회피법 한 줄>.
- **<다른 증상>**: <원인>. <해결>.
- **<구성 제약>**: <어떤 설정을 함께 두지 않으면 실패하는가>. <함께 둬야 할 것>.

증상이 없는 "알면 좋은 팁" 은 이 섹션 말고 README 이나 comment 로.

## 환경별 분리 필요 항목

환경마다 달라야 하는 값 (dev vs prod, active_env 별) 을 표로 정리합니다.
`values-<env>.yaml` 또는 `cluster-env-inject` 가 채울 항목을 한 눈에
보기 위함.

| 항목 | dev | prod |
|------|-----|------|
| `<config.key>` | `<dev value>` | `<prod value>` |
| `resources.requests.memory` | `<value>` | `<value>` |
| `replicaCount` | `<value>` | `<value>` |

환경이 셋 이상이면 열을 추가합니다. 환경별로 달라질 값이 없으면
"환경별 차이 없음" 한 줄로 대체하거나 섹션 자체를 삭제하세요.
