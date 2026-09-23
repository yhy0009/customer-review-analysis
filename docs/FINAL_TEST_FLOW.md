# 샘플 CSV를 이용한 최종 기능 테스트

`data/sample_reviews.csv`는 직접 작성한 **합성 데이터 40행**이다. 실제 고객 정보는 포함하지 않는다.
한국어·영어·한영 혼합 리뷰, 5개 제품, 전자·생활·뷰티 카테고리를 포함한다.
파일은 UTF-8 BOM, 쉼표 구분 형식이며 첫 줄은 헤더다.

## 샘플 구성과 예상 결과

| 구분 | review_id | 행 수 | 예상 처리 |
|---|---|---:|---|
| 일반 리뷰 | S001~S030 | 30 | 모두 정제 성공 |
| 선택 항목 누락 | S031~S034 | 4 | 모두 정제 성공, 누락값 유지 |
| 잘못된 별점 | S035 | 1 | 6점이므로 INVALID_RATING |
| 잘못된 날짜 | S036 | 1 | 2026-02-30이므로 INVALID_REVIEW_DATE |
| 빈 본문 | S037 | 1 | MISSING_REVIEW_TEXT |
| 한 글자 본문 | S038 | 1 | 최소 길이 3 기준 REVIEW_TOO_SHORT |
| 동일 ID·내용 재등장 | 마지막의 S001, S014 | 2 | import --policy skip에서 중복 건너뛰기 |

- 새 DB에 가져오기: `processed=40 succeeded=38 skipped=2 failed=0 rejected=0`
- 정제: `processed=38 succeeded=34 skipped=0 failed=0 rejected=4`
- 정제 리뷰 34건 중 제품명·작성일·별점은 각각 32건에 존재한다. 평균 별점은 **3.00**이다.
- 알려진 제품은 5개이며, 이름 없는 리뷰는 별도 그룹 2건이다.
- 카테고리별 건수: 전자 14, 생활 13, 뷰티 6, 카테고리 없음 1.
- 감정과 생성 문구는 AI 결과이므로 정확한 감정별 건수·문장을 고정된 성공 조건으로 사용하지 않는다.

아래는 **macOS/Linux의 새 DB와 같은 터미널 세션**을 기준으로 한다. 설치·설정을 마친 뒤
프로젝트 루트에서 명령을 순서대로 한 줄씩 실행한다. 설치 방법은 [프로젝트 README](../README.md)를 참고한다.

## 1. 환경과 테스트 저장 위치 준비

```bash
source .venv/bin/activate

export CRA_TEST_RUN="manual-$(date +%Y%m%d-%H%M%S)"
export CRA_DATABASE_PATH="data/final-tests/$CRA_TEST_RUN.db"
export CRA_OUTPUT_DIR="output/final-tests/$CRA_TEST_RUN"
export CRA_LOG_FILE="$CRA_OUTPUT_DIR/app.log"
```

매번 새 이름을 사용하므로 기존 리뷰 DB와 섞이지 않는다. 다시 시작하려면 위 환경변수를
새 시각으로 설정한다. 테스트 도중 같은 DB를 계속 쓰려면 준비 단계를 반복하지 않는다.
종료 후에는 다음으로 테스트 경로 설정을 해제할 수 있다.

```bash
unset CRA_TEST_RUN CRA_DATABASE_PATH CRA_OUTPUT_DIR CRA_LOG_FILE
```

위 `unset` 명령은 **테스트를 모두 마친 뒤** 실행한다.

`analyze`·`extract` 단계 전에는 프로젝트 `.env` 또는 기존 설정에 유효한 `AI_API_KEY`와
사용 중인 제공자·모델이 설정되어 있어야 한다. 이미 사용하던 설정이 있다면 그대로 사용한다.
이 두 명령이 실제 AI 요청을 보내며 사용량이 발생한다. 나머지 아래 CLI 명령은 API 키가 필요 없다.

## 2. 가져오기와 정제

```bash
python main.py import --file data/sample_reviews.csv --policy skip
python main.py clean --policy skip --min-length 3
python main.py stats
```

정제 명령은 의도적으로 넣은 오류 4건 때문에 **종료 코드 1**을 반환한다. 위 표와 같은
제외 사유라면 정상이다. `&&`로 연결하면 이 지점에서 멈추므로 각 명령을 따로 실행한다.

성공 조건: 원본 38건, 정제 34건, 분석 0건, 평균 별점 3.00.
정제 전의 `stats`가 0건인 것은 정상이며 `stats`는 Raw 건수를 표시하지 않는다.

## 3. 목록·상세·선택 항목 누락 확인

```bash
python main.py list --sort id --order asc --size 50
python main.py show --id 31
python main.py show --id 32
python main.py show --id 33
python main.py show --id 34
python main.py list --sort date --order desc --size 50
python main.py list --product "무선 이어폰 A" --rating 2
```

`show --id`는 CSV의 `S031` 같은 외부 ID가 아닌 **DB 내부 숫자 ID**를 받는다.
새 DB에서는 S031~S034가 내부 ID 31~34에 해당한다.

| 내부 ID | 확인할 내용 |
|---:|---|
| 31 | 제품명 없음, 날짜·별점 존재 |
| 32 | 날짜 없음, 제품명·별점 존재 |
| 33 | 별점 N/A, 제품명·날짜 존재 |
| 34 | 제품명 없음·날짜 없음·별점 N/A, 본문은 보존 |

날짜 정렬에서는 작성일 없는 32·34번이 마지막에 나온다.

## 4. AI 분석: 1건 확인 후 나머지 진행

```bash
python main.py analyze --id 1
python main.py show --id 1
python main.py analyze --unanalyzed
python main.py stats
python main.py show --id 2
python main.py show --id 5
python main.py show --id 34
```

첫 1건이 성공하면 나머지 33건을 분석한다. 최종 성공 조건은 정제 34건·분석 완료 34건·
미분석 0건·실패 0건·완료율 100%다. 감정·신뢰도·한국어 요약·키워드와 원문을 함께 확인한다.
2번은 영어, 5번은 한영 혼합, 34번은 선택 항목 없이 본문만 있는 리뷰다.

일부 실패 시 오류와 로그를 확인하고 원인을 해결한 뒤 `analyze --unanalyzed`로 재시도한다.
이미 성공한 리뷰는 이 명령에서 제외된다. 전체 재분석이 필요하지 않으면 `--force`를 붙이지 않는다.

## 5. 인사이트 저장 → 대시보드 리포트 연결

```bash
python main.py extract --limit 50
python main.py dashboard --html --output "$CRA_OUTPUT_DIR/reports"
python main.py dashboard --report-format txt --output "$CRA_OUTPUT_DIR/reports-txt"
```

`extract`는 분석 완료 34건을 사용하고 마지막에 `인사이트 파일: ...json`을 출력한다.
`dashboard`는 같은 조건의 저장 결과를 읽으며 AI를 다시 호출하지 않는다.

확인할 항목:

- 콘솔에 `리포트에 저장된 AI 인사이트를 포함했습니다`와 실제 추출 대상 34건이 표시된다.
- `reports/`에 PNG·Markdown·HTML, `reports-txt/`에 PNG·TXT가 생성된다.
- 리포트와 HTML에 요약·이슈·개선 제안·근거 리뷰 ID·인용문이 표시된다.
- HTML의 차트·요약·근거를 확인하고 인용문이 해당 리뷰 원문에 있는지 확인한다.
- 통계는 34건을 포함하되 날짜·별점별 차트는 값이 있는 32건을 집계한다.

macOS에서 보고서 폴더를 열려면:

```bash
open "$CRA_OUTPUT_DIR/reports"
```

인사이트가 포함되지 않으면 콘솔 사유를 확인한다. 데이터 재분석 또는 모델 변경 후에는
`extract`를 다시 실행해야 한다. 제품·기간 필터를 추가할 때는 두 명령에서 동일하게 지정한다.

## 6. 비교·내보내기

```bash
python main.py compare --output "$CRA_OUTPUT_DIR/comparison-product" --chart
python main.py compare --group-by category --output "$CRA_OUTPUT_DIR/comparison-category" --chart

python main.py export --format csv --output "$CRA_OUTPUT_DIR/reviews.csv"
python main.py export --format jsonl --output "$CRA_OUTPUT_DIR/reviews.jsonl"
python main.py export --format excel --output "$CRA_OUTPUT_DIR/reviews.xlsx"
```

비교 표·CSV·JSON·PNG와 내보내기 3개 파일을 확인한다. 내보낸 데이터는 각각 34행이며,
CSV/Excel은 헤더를 제외한 행 수다. 누락값은 CSV/Excel에서 빈 셀, JSONL에서 `null`이어야 한다.
분석한 감정·신뢰도·요약·키워드도 포함된다. 같은 파일명으로 재실행하려면 `--force`가 필요하다.

## 7. 선택적 추가 확인

### 중복 재적재와 정제 재실행

```bash
python main.py import --file data/sample_reviews.csv --policy skip
python main.py clean --policy skip --min-length 3
python main.py stats
```

재적재는 `processed=40 succeeded=0 skipped=40`, 정제는
`processed=38 succeeded=0 skipped=34 failed=0 rejected=4`가 예상된다.
정제 34건과 기존 분석 결과가 유지되어야 한다. 정제의 종료 코드 1은 앞서 설명한 동일한 오류 샘플 때문이다.

### 고정된 샘플 기간의 감정 변화 알림

```bash
python main.py dashboard --date-to 2026-09-22 --no-insights --html \
  --output "$CRA_OUTPUT_DIR/alerts"
```

9월 9~15일과 16~22일을 비교한다. 날짜가 없는 리뷰는 제외한다. 실제 감정 판정에 따라
경고·정상·판정 보류가 결정되며, 실행 날짜가 바뀌어도 비교 기간은 고정된다.
이 단계는 앞선 전체 범위 인사이트와 별도로 날짜 필터·알림을 확인한다.

### 웹 대시보드

5단계의 새 테스트 출력 폴더에는 `extract --limit 50` 결과 JSON 하나가 있다.
다음 명령으로 그 파일을 찾아 같은 DB의 웹 대시보드에 연결할 수 있다.

```bash
CRA_TEST_INSIGHT_FILE="$(python -c 'import os; from pathlib import Path; print(next((Path(os.environ["CRA_OUTPUT_DIR"]) / "insights").glob("*/*.json")))')"
python scripts/serve_dashboard.py --database "$CRA_DATABASE_PATH" \
  --insight-file "$CRA_TEST_INSIGHT_FILE" --port 8765
```

브라우저에서 `http://127.0.0.1:8765`를 열고 전체 조건에서 34건·저장된 인사이트·리뷰 목록·
리포트 다운로드를 확인한다. 제품·기간 필터 변경 후에는 기존 전체 인사이트가 범위 불일치로
제외되는지 확인한다. 종료는 터미널에서 `Ctrl+C`를 누른다. 위 명령은 생성 버튼을 활성화하지 않는다.

## 완료 기준과 사전 검증

- [ ] 샘플 가져오기 38건, 중복 2건 건너뛰기
- [ ] 정제 34건 성공, 의도한 4건만 제외
- [ ] 선택 항목 누락 4건 보존, 평균 별점 3.00
- [ ] 실제 AI 분석 34건 성공 및 다국어 결과 확인
- [ ] 인사이트 JSON 저장, TXT/MD/HTML에 같은 추출 결과 포함
- [ ] 제품·카테고리 비교 및 CSV/JSONL/Excel 34건 내보내기
- [ ] 재적재 후 기존 분석 결과 유지

샘플 작성 시 임시 DB에서 실제 CLI 호출 14개로 가져오기·정제·조회·비교·차트·HTML·
내보내기·중복 재실행을 검증했다. 이 사전 검증에서는 AI API를 호출하지 않았다.
실제 제공자를 통한 분석·추출 결과는 위 4~5단계에서 확인한다.
