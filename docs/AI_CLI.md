# 분석·인사이트 CLI 실행

`main.py`에서 `analyze`와 `extract`를 기본 실행할 수 있다. 공통 설정으로 SQLite와
기존 AI 서비스를 구성하며, 명령별 요청·결과 모델과 제공자·재시도 구현을 재사용한다.

## 실행 준비

`requirements.txt`의 의존성을 설치하고 `.env.example`을 참고해 사용하는 제공자의
`AI_API_KEY`를 설정한다. 설정 파일의 `ai`와 환경변수 `AI_PROVIDER`, `AI_MODEL`,
`AI_API_KEY`, `AI_BASE_URL`, `AI_REASONING_EFFORT`를 기존 우선순위대로 사용한다.
공식·호환 제공자의 설정 차이는 [AI 분석 안내](AI_ANALYSIS.md)를 따른다.

상대 설정·환경 파일·로그·DB 경로는 프로젝트 루트 기준이다. `--config`, `--log-level`은
명령 앞에 지정한다. 분석에는 저장된 Clean 리뷰가, 추출에는 분석 완료 리뷰가 필요하다.
Raw 리뷰만 있는 경우 대상에 포함되지 않는다. `import`는 [원본 리뷰 적재 안내](IMPORT.md)에
따라 기본 실행할 수 있다. 이후 `python main.py clean`으로 정제하면 분석 대상이 준비된다.
정제 옵션과 제외 상태는 [리뷰 정제 안내](CLEAN.md)를 따른다.
분석 결과는 `python main.py dashboard`로 PNG·리포트에 저장할 수 있다([대시보드 CLI 안내](DASHBOARD.md)).
이 명령은 추가 AI 호출 없이 저장된 통계를 사용하며 `extract` 결과를 자동으로 포함하지 않는다.

## 감정 분석

```bash
python main.py analyze --unanalyzed --limit 20
python main.py analyze --id 2
python main.py analyze --id 2 --force
python main.py analyze --all --limit 20
python main.py --config config/config.json analyze --unanalyzed
```

`--all`, `--id`, `--unanalyzed` 중 하나를 선택한다. `--unanalyzed`는 이전 실패를 포함한다.
`--force`는 선택한 대상만 다시 분석하며, 미분석 선택을 전체 리뷰로 넓히지 않는다.
`--all --limit N`은 기존 분석 결과가 있는 리뷰도 N건에 포함하므로 새 API 호출 건수는
N보다 적을 수 있다. 이미 분석된 리뷰는 `--force`가 없으면 건너뛴다.

완료 시 다음과 같이 처리 항목 수를 출력한다. API 재시도 횟수와는 다르다.

```text
processed=3 succeeded=2 skipped=1 failed=0
```

성공 건은 즉시 저장된다. AI 행 오류는 실패 상태로 기록하고 다음 리뷰를 처리한다.
설정·저장소 오류는 중단하지만 이미 저장한 성공 건은 유지한다. 재분석 실패 시 이전
결과 제거 등 기존 배치 계약은 [AI 분석 안내](AI_ANALYSIS.md)를 따른다.

## 인사이트 추출

```bash
python main.py extract
python main.py extract --sentiment negative --limit 20
python main.py extract --product 이어폰 --date-from 2026-09-01 --date-to 2026-09-30
```

조건에 맞는 분석 완료 리뷰를 ID 오름차순으로 선택한 뒤 `--limit`을 적용한다.
미분석·실패 리뷰는 제외한다. 출력에는 UTC 생성 시각, 필터, 실제 대상 수, 긍·부정
키워드별 리뷰 수, 요약, 주요 이슈와 개선 제안이 포함된다. 콘솔에 표시하는 외부 문자열의
터미널 제어 문자를 제거하며, 생성 결과를 DB나 파일에 자동 저장하지 않는다.

추출 대상이 0건이면 안내만 출력하고 API를 호출하지 않는다. 입력 JSON을 기본
24,000자 이내의 근거 배치로 분할한다(최대 20건/배치, 기본 100배치). 단일 리뷰 또는 최종 요약 입력이 한도를 넘으면 오류를 반환한다.
현재 추출은 리뷰별 근거 배치 B회와 최종 요약 1회를 요청한다. 모든 요청은 DB 읽기
트랜잭션이 종료된 뒤 실행되며, 근거 누락·형식 오류는 실패한 단계에서만 제한적으로 재시도한다.
공식 제공자의 추출 출력 상한은 단계당 8,192토큰이고, 호환 제공자는 계속 model/messages만
전송한다. 공통 `ai.timeout_seconds` 기본은 요청당 90초이며 명시한 설정값을 우선한다.
키워드 집계·선택 규칙은 [인사이트 추출 안내](INSIGHT_EXTRACTION.md)를 따른다.

## 실행 경계와 종료 코드

### 인사이트 추출 진단 로그

`extract`는 `customer_review_analysis.insight_extractor` 로그에 요청 시작·성공과 실패를 기록한다.
기존 `logging.level`·`logging.file` 설정을 사용하며, `CRA_LOG_FILE`이 있으면 해당 경로로 기록한다.
기본 INFO에서 시작·성공, WARNING에서 재시도할 실패, ERROR에서 최종 실패를 확인할 수 있다.

```text
Insight request failed: stage=summary batch=1/1 code=INSIGHT_ISSUE_MISSING http_status=none attempt=2/4 retries=1 will_retry=True
```

- `stage=evidence`: 리뷰별 근거 추출과 검증. `batch=2/3`은 총 3개 근거 배치 중 2번째다.
- `stage=summary`: 최종 요약·이슈·개선 제안 생성과 검증. 요약 요청은 `batch=1/1`이다.
- `attempt=2/4`: 최대 4회 시도 중 2번째. `retries=1`은 첫 호출을 제외한 재시도 횟수다.
- `will_retry`: 해당 실패 뒤 다시 요청할지 여부. 재시도 불가 오류는 첫 시도에 종료될 수 있다.
- `http_status`: HTTP 응답이 있는 경우 숫자 상태 코드. 응답 검증·연결 오류 등은 `none`이다.

| 코드 | 의미 |
|---|---|
| `AI_TIMEOUT`, `AI_CONNECTION_FAILED` | 요청 시간 초과 / 연결 실패 |
| `AI_HTTP_ERROR`, `AI_RATE_LIMITED`, `AI_QUOTA_EXCEEDED` | HTTP 요청 오류 / 요청 제한 / 할당량 부족 |
| `AI_OUTPUT_LIMIT`, `AI_REFUSAL`, `AI_OUTPUT_INTERRUPTED` | 출력 길이 제한 / 응답 거부 / 그 밖의 출력 중단 |
| `AI_RESPONSE_INVALID`, `AI_ERROR` | 응답 구조 오류 / 분류되지 않은 제공자 오류 |
| `EVIDENCE_FORMAT_INVALID`, `EVIDENCE_REVIEW_MISMATCH` | 근거 JSON·필드 오류 / 리뷰 수·순서 불일치 |
| `EVIDENCE_QUOTE_MISMATCH`, `EVIDENCE_COMPLAINT_MISSING` | 원문에 없는 인용 / 부정 리뷰의 불편 근거 누락 |
| `INSIGHT_FORMAT_INVALID` | 최종 응답 형식·길이 제한 위반 |
| `INSIGHT_ISSUE_MISSING`, `INSIGHT_PRAISE_MISSING` | 추출한 불편 / 장점이 최종 결과에서 누락 |
| `INSIGHT_UNGROUNDED_ISSUE`, `INSIGHT_SUGGESTION_INVALID` | 근거 없는 이슈 / 허용된 후보 외 개선 제안 |

최종 CLI 오류에도 단계·배치·코드·실제 시도/재시도 횟수와 사용 가능한 HTTP 상태를 표시한다.

```text
[ERROR] AI 인사이트 추출에 실패했습니다. (stage=summary, batch=1/1, code=INSIGHT_ISSUE_MISSING, attempts=4, retries=3)
```

로그에는 API 키, 서버 주소, 제품명·리뷰·프롬프트·응답 원문, 외부 예외 메시지를 기록하지 않는다.
제공자 오류는 `AIProviderError`의 `AIErrorCode` 열거형과 100~599의 정수 HTTP 상태만
출력 경계에서 허용한다. 임의 문자열 코드나 잘못된 상태는 `AI_ERROR`/`none`으로 대체한다.
재시도 정책·대기 시간·응답 검증 기준·최종 종료 코드 4는 유지한다.

### 실행과 종료 코드 규칙

- 요청 인자를 검증한 뒤 AI 설정·서비스를 구성하고 DB 연결을 연다. 명령이 끝나거나
  오류가 발생하면 연결을 닫는다. 추출은 읽기 스냅샷을 주입하고 AI 호출 전에 종료한다.
- AI SDK는 `analyze`·`extract` 실행 시에만 불러온다. 조회와 CSV·JSONL 내보내기는
  AI SDK 없이 실행할 수 있고, 도움말에도 AI SDK가 필요 없다.
- AI SDK 누락은 설치 안내와 코드 2로 처리한다. SDK가 설치돼 있고 설정이 유효하면
  빈 대상이나 분석 완료 리뷰만 건너뛰는 실행은 API 키 없이도 성공한다.
  실제 API 요청이 필요할 때 키가 없으면 코드 2다.
- 각 AI 명령은 해당 모듈의 기본 프롬프트 버전을 사용한다. `ai`의 알려진 공통 필드만
  `AnalysisOptions`로 전달하므로 향후 확장용 설정 키가 있어도 생성자 오류가 나지 않는다.
- 명시적으로 주입한 핸들러와 빈 매핑 `{}`는 기본 매핑으로 대체하지 않는다.

| 종료 코드 | 의미 |
|---:|---|
| 0 | 성공, 빈 결과 또는 기존 분석 건너뛰기 포함 |
| 1 | 분석 배치의 일부 또는 전체 행 실패 |
| 2 | 잘못된 인자·설정·분석 ID 또는 AI 의존성 누락 |
| 3 | 저장소 접근·스키마·읽기·쓰기 오류 |
| 4 | 추출 AI 오류 또는 배치 결과로 처리되지 않은 AI 오류 |

## 검증

```bash
python -m unittest discover -s tests -p 'test_ai_cli.py' -v
python -m unittest discover -s tests -p 'test_insight_output.py' -v
python -m unittest discover -s tests -q
```

임시 설정과 SQLite에서 기본 `main()` 경로를 실행하고 AI SDK 응답을 대체해 대상 선택,
저장·재분석·실패, 추출 필터, 설정 전달과 연결 수명을 검증한다. 별도 프로세스의
`python -S` 실행으로 AI SDK 없는 명령과 누락 오류도 확인한다. 실제 AI API는 호출하지 않는다.

대량 입력은 주요 불편 3개의 요약 범위와 전체 근거 주제·인용을 함께 표시한다.
세부 선정·병합 규칙은 [인사이트 추출 안내](INSIGHT_EXTRACTION.md)를 따른다.
