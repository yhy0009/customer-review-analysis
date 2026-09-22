# 제품·카테고리별 비교 분석

`compare`는 저장된 정제 리뷰와 감정 분석 결과를 제품 또는 카테고리별로 집계한다.
CLI 표와 CSV·JSON, 선택적 PNG 비교 차트를 제공한다. AI API 호출이나 API 키는 필요 없다.
감정 분석 결과가 없으면 건수·별점은 비교할 수 있고 감정 비율은 `N/A`로 표시한다.

## 실행

```bash
# 전체 제품 비교 (이름 오름차순)
python main.py compare

# 선택한 제품 두 개를 같은 기간으로 비교
python main.py compare --name "이어폰 A" --name "이어폰 B" \
  --date-from 2026-09-01 --date-to 2026-09-30

# 카테고리 비교 + CSV·JSON·PNG 저장
python main.py compare --group-by category --output output/comparison --chart

# 특정 카테고리 안에서 부정 비율이 높은 제품부터 비교
python main.py compare --category 전자 --sort negative --order desc

# 선택한 카테고리만 비교
python main.py compare --group-by category --name 전자 --name 도서
```

| 옵션 | 기본값 | 의미 |
|---|---|---|
| `--group-by product\|category` | `product` | 제품별 또는 카테고리별 집계 |
| `--name 이름` | 모든 그룹 | 선택할 그룹 이름. 여러 번 지정 가능 |
| `--category 이름` | 전체 | 해당 카테고리의 리뷰만 사용 |
| `--date-from`, `--date-to` | 전체 | 리뷰 작성일 기준, 양 끝 포함 |
| `--rating-min 1..5` | 전체 | 최소 별점. 조건에 맞는 리뷰만 모든 지표에 포함 |
| `--sort name\|reviews\|rating\|negative` | `name` | 이름·리뷰 수·평균 별점·부정 비율 정렬 |
| `--order asc\|desc` | `asc` | 오름차순·내림차순. 계산 불가 값은 항상 마지막 |
| `--min-reviews N` | `5` | 분석 완료 건수가 이 값 미만이면 표본 부족 안내 |
| `--output 디렉터리` | 저장하지 않음 | CSV·JSON 저장. 상대 경로는 프로젝트 루트 기준 |
| `--chart` | 생략 | PNG 비교 차트 추가. `--output` 필요 |
| `--force` | 생략 | 같은 이름의 기존 산출물 교체. `--output` 필요 |

제품·카테고리 이름은 NFKC Unicode 정규화와 앞뒤·연속 공백 정리 후 **정확히 일치**해야 한다.
대소문자는 구분하고 `%`, `_`도 일반 문자다. 기존 `list --product`의 부분 검색과 다르다.
중복 `--name`은 한 번만 적용한다. 현재 기간·별점·카테고리 조건에 없는 이름은 별도로 안내하며
다른 제품으로 대체하거나 0% 감정 비율로 표시하지 않는다.

## 카테고리 입력과 기존 데이터

CSV·Excel에 아래 중 하나의 선택 열을 추가하면 된다.

- `category`, `product_category`, `카테고리`, `상품분류`
- 열 이름의 영문 대소문자·공백·밑줄·하이픈 차이는 허용한다.

```csv
review_id,product_name,review_date,rating,review_text,category
a-1,이어폰 A,2026-09-01,5,음질과 착용감이 좋습니다,전자
a-2,이어폰 A,2026-09-02,1,연결이 자주 끊깁니다,전자
b-1,이어폰 B,2026-09-02,4,가격 대비 만족합니다,전자
c-1,도서 A,2026-09-03,3,설명이 보통입니다,도서
d-1,기타 제품,2026-09-03,4,배송이 빨랐습니다,
```

```bash
python main.py import --file data/comparison_reviews.csv
python main.py clean
python main.py compare --group-by category --output output/comparison --chart
```

감정 비율까지 비교하려면 기존 `analyze` 명령으로 분석 결과를 저장해야 한다. AI 분석 실행에는
별도의 API 설정이 필요하며 `compare`가 분석을 자동 실행하지는 않는다.

기존 수집기는 추가 열을 `RawReview.raw_payload`에 보존한다. 비교 저장소는 이 메타데이터를
정제 리뷰 ID와 연결해 읽으므로 **DB 스키마 변경이나 재적재 없이** 이미 저장된 카테고리 열도
사용할 수 있다. 카테고리가 원본에 없으면 추측하지 않고 `[카테고리 없음]` 그룹에 포함한다.
JSON의 `name: null`, CSV의 `uncategorized: True`로 실제 이름과 구분할 수 있다.

카테고리는 리뷰 행마다 하나다. 같은 제품이 여러 카테고리로 입력되어 있으면 카테고리 비교에서
각 리뷰가 해당 분류로 들어간다. 빈 값은 미분류이며 문자열 또는 유한한 숫자만 허용한다.
서로 다른 카테고리 별칭 열에 다른 값이 있거나 목록·객체 같은 잘못된 값이 있으면 문제 리뷰 ID와
함께 오류를 반환한다. 카테고리를 사용하지 않는 제품 비교에는 해당 메타데이터가 영향을 주지 않는다.

카테고리를 수정해 재적재할 때도 기존 `import --policy upsert` 규칙이 적용된다. 원본 변경은
해당 정제·분석 결과를 무효화하므로 다시 `clean`하고 필요한 경우 `analyze`해야 한다.

## 지표 정의

| 지표 | 기준 |
|---|---|
| 제품 수 | 해당 그룹 안의 서로 다른 정규화된 제품명 수 |
| 리뷰 수 | 필터에 맞는 정제 리뷰 수. Raw만 있거나 정제에서 거절된 행은 제외 |
| 분석·미분석·실패 | 기존 저장소 상태를 각각 집계 |
| 평균 별점 | 미분석·실패를 포함한 정제 리뷰 별점의 평균 |
| 분석 완료율 | 분석 완료 건수 / 정제 리뷰 수 |
| 긍정·중립·부정 비율 | 해당 감정 건수 / 분석 완료 건수 |
| 표본 부족 | 분석 완료 건수 < `--min-reviews`. 그룹을 제외하지 않고 안내 |

카테고리 평균·비율은 **리뷰별로 합산**한다. 제품 평균을 다시 평균내지 않는다.
예를 들어 한 제품에 4건, 다른 제품에 1건이면 5건의 리뷰를 기준으로 계산한다.
분석 0건은 감정 비율을 CLI·차트에서 `N/A`, JSON에서 `null`, CSV에서 빈 값으로 표시한다.
실제 부정 리뷰가 0건이고 분석 건수가 있다면 부정 비율은 0이다.

같은 기간에도 리뷰 수·분석 완료율·고객 구성이 달라질 수 있다. 이 기능은 기술 통계 비교이며
제품의 우열이나 통계적 유의성을 판정하지 않는다. 감정 필터로 분모가 왜곡되지 않도록
`--sentiment` 옵션은 제공하지 않는다.

## 산출물과 파일 보호

```text
output/comparison/
  comparison_category_20260922_010203.csv
  comparison_category_20260922_010203.json
  comparison_category_20260922_010203_01.png
  comparison_category_20260922_010203_02.png  # 그룹이 20개를 넘으면 다음 장
```

같은 실행은 동일한 UTC 시각을 파일명에 사용한다. CSV·JSON은 모든 선택 그룹을 포함한다.
PNG는 20그룹씩 나누어 리뷰 수·평균 별점·감정 비율을 함께 표시하고, 긴 이름은 차트에서만
축약한다. 원래 그룹명은 CSV·JSON에 보존한다. 빈 결과도 헤더·빈 배열·안내 차트를 생성한다.
`visualization.font_family`, `visualization.dpi` 설정을 사용한다.

JSON에는 적용 조건·선택 이름·표본 기준·없는 이름·생성 시각과 그룹별 지표가 포함된다.
CSV는 UTF-8 BOM을 사용하고 스프레드시트 수식처럼 보이는 이름에 작은따옴표를 붙인다.
JSON에는 원래 그룹명을 보존한다. 기계 판독용 비율은 0~1 범위이며 CLI·차트만 퍼센트로 표시한다.

모든 파일을 임시 디렉터리에서 생성한 후 게시하므로 차트 생성 실패가 기존 CSV·JSON을 교체하지
않는다. 기본적으로 기존 파일을 덮어쓰지 않으며 심볼릭 링크·디렉터리와 SQLite DB 파일을 보호한다.
최종 게시는 파일별로 원자적이다. 전체 파일이 하나의 트랜잭션은 아니므로 중간 게시 실패 시
이미 저장된 파일 경로를 오류에 포함하며 `--force`로 교체한 이전 파일을 자동 복원하지 않는다.

| 종료 코드 | 의미 |
|---|---|
| 0 | 조회·요청한 파일 생성 성공. 빈 결과·표본 부족도 포함 |
| 2 | 인자·설정 오류 또는 차트 의존성 누락 |
| 3 | 저장소·카테고리 메타데이터·출력 오류 |

## 코드 경계 및 검증

- `src/comparison.py`: 요청·결과 모델, 비교 저장소 계약, 선택·정렬 서비스
- `SQLiteReviewRepository.get_comparison_groups`: 한 번의 SQL 조회로 Raw/Clean/Analysis를 읽고 그룹별 기존 통계 집계 재사용
- `src/comparison_cli.py`: CLI 인자 검증·연결·종료 코드
- `src/comparison_output.py`: CLI·CSV·JSON 및 파일 게시
- `src/comparison_charts.py`: 선택적 PNG 비교 차트

조회 자체는 DB 내용을 변경하지 않는다. 기존 CLI와 동일하게 처음 연결한 빈 DB는 초기화한다.
별도 `ComparisonRepository` 계약을 사용하므로 기존 `ApplicationServices` 주입 객체를 확장할
필요가 없다. `compare`는 기본 런타임에 독립적으로 등록된다.

```bash
python -m unittest tests.test_comparison tests.test_comparison_output tests.test_comparison_cli -q
python -m unittest discover -s tests -q
```

실제 SQLite의 분모·가중 집계·필터·누락 분류·상태 무효화, CSV/Excel → import → clean → compare,
선택 이름·정렬·빈 결과·다른 작업 디렉터리 실행, API·선택 패키지 없는 조회·CSV/JSON,
PNG 분할·긴 이름·빈 차트, 파일 충돌·보호·실패 정리를 검증한다.
