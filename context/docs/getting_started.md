# Harness 사용 가이드

> 내부 동작 구조는 `harness_internals.md` 참고.

---

## 1. 준비

**CLI 도구** (`GikView/config/tools.yaml` 버전 기준): kubectl, helm, kubeconform, trivy, gitleaks

**Python 환경**:
```bash
cd /home/netai/GikView
python3.12 -m venv .venv && .venv/bin/pip install -e .
```

**환경변수** (`GikView/.env`):
```bash
GEMINI_API_KEY=...        # llm.yaml의 api_key_env 값과 일치
ANTHROPIC_API_KEY=...     # claude 프로파일 사용 시
```

---

## 2. 설정 파일

실행 전 아래 파일들이 존재해야 한다.

| 파일 | 위치 | 비고 |
|------|------|------|
| `cluster.yaml` | `gikview/config/` | gitignored — 직접 생성 |
| `kagent.yaml` | `gikview/config/` | kagent MCP URL + 툴 목록 |
| `build.yaml` | `gikview/config/` | Docker 레지스트리 (이미지 빌드 시) |
| `llm.yaml` | `GikView/config/` | LLM 프로바이더/모델/프로파일 |

`cluster.yaml`은 gitignored이므로 환경마다 직접 작성한다. 구조는 `harness/config.py` 상단 주석 참고.

---

## 3. 실행

```bash
cd /home/netai/GikView
.venv/bin/python scripts/run.py --phase <phase> --sub-goal <sub_goal>
```

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--phase` | 필수 | `gikview/context/phases/<phase>.md` 에 대응 |
| `--sub-goal` | 필수 | phase 문서 내 섹션 헤딩에 fuzzy 매칭 |
| `--max-retries N` | 3 | Developer 최대 재시도 |
| `--max-runtime-retries N` | 3 | Runtime Verifier 자가 루프 최대 횟수 |
| `--skip-interrupt` | — | interrupt 없이 자동 진행 (CI용) |

---

## 4. Interrupt 대응

### Developer 직전
```
추가 지시사항을 입력하세요 (없으면 Enter, 중단은 'abort'):
```
- Enter: 그대로 진행
- 텍스트: 이번 시도에만 LLM에 전달
- `abort`: 중단

### Runtime Verifier 직후 (실패 시)
```
재시도하려면 Enter (또는 힌트 입력), 중단하려면 'abort':
```
- `failure_source: smoke_test` 진단 시 → 자동 재시도 중단, 강제 개입 요구

---

## 5. 프로젝트 파일 작성

새 sub-goal 추가 시 필요한 파일:

| 파일 | 경로 | 작성자 |
|------|------|--------|
| Phase 문서 | `gikview/context/phases/<phase>.md` | 사람 |
| Knowledge 파일 | `gikview/context/knowledge/<tech>.md` | 사람 |
| Smoke test | `gikview/edge-server/tests/<phase>/smoke-test-<sub_goal>.sh` | 사람 |

Phase 문서 필수 필드:
```markdown
- **service_name**: `emqx`
- **technology**: `emqx`      ← knowledge 파일 조회 키
- **dependency**: `cert-manager`
```

> 형식 전체는 `gikview/context/phases/_template.md` 참고.
