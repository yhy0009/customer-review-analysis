# 대시보드 CLI

`python main.py dashboard`는 설정된 SQLite의 정제 리뷰와 저장된 분석 결과를 집계해
통합 PNG 한 장과 UTF-8 리포트 하나를 만든다. API 키나 AI 호출 없이 실행할 수 있다.
Matplotlib 등 `requirements.txt`의 패키지는 설치해야 한다.

## 실행

```bash
# 설정의 paths.output_dir 사용 (기본값 output), Markdown 리포트
python main.py dashboard

# 제품명 부분 일치 + 양 끝 날짜를 포함하는 기간 필터
python main.py dashboard --product 이어폰 --date-from 2026-09-01 --date-to 2026-09-30 \
  --output output/september --report-format txt

# 같은 이름의 파일이 있을 때 교체 허용
python main.py dashboard --output output/september --report-format txt --force

# 전역 옵션은 명령 앞에 지정
python main.py --config config/config.json dashboard --output output/reports
```

`--output`은 디렉터리다. 생략하면 `paths.output_dir`을 사용하고 상대 경로는 프로젝트
루트 기준으로 해석한다. 디렉터리는 자동 생성하며 `output/charts.png`처럼 점이 들어가도
디렉터리로 처리한다. 기존 파일 경로를 디렉터리로 지정하면 종료 코드 3이다.

## 결과

```text
output/
  dashboard_20260922_010203.png
  report_20260922_010203.md
```

파일명은 한 번 결정한 UTC `YYYYMMDD_HHMMSS`를 공유한다. 명령이 성공하면 stdout에
실제로 생성한 파일의 종류·포맷·절대 경로가 표시된다.

- PNG: 감정 분포, 날짜별 감정 추이, 별점별 감정 매트릭스와 긍정·부정 리뷰 키워드.
- 리포트: 정제·분석·미분석·실패 건수, 평균 별점, 분석 완료율과 통계·키워드.
  `--report-format md`가 기본이며 `txt`도 지원한다.
- 데이터가 없거나 필터 결과가 0건이어도 빈 차트·리포트를 생성한다.
  Raw만 적재한 리뷰는 포함되지 않으므로 먼저 `clean`을 실행한다.
- 미분석·실패 리뷰도 정제 리뷰 건수와 평균 별점에 포함된다. 감정·키워드는 분석 완료
  리뷰를 기준으로 집계한다. 분석이 없으면 `analyze` 후 다시 생성한다.
- 이 명령은 `extract`를 실행하거나 인사이트 캐시를 읽지 않는다. 리포트에는
  `AI 인사이트가 제공되지 않았습니다`가 표시된다. AI 요약까지 포함하는 리포트는
  [웹 대시보드](WEB_DASHBOARD.md) 또는 [리포터 단독 호출](REPORT_GENERATION.md)을 사용한다.

필터는 두 산출물에 동일하게 적용한다. 현재 통계 DTO에는 필터 정보가 없으므로
리포트 본문에 필터 문자열은 표시되지 않는다. 조건별 출력 디렉터리로 구분할 수 있다.

## 감정 변화 알림

명령 실행 시 기존 일별 통계로 최근 N일과 직전 N일의 부정 리뷰 비율을 비교하고,
생성 파일 경로에 이어 **CLI stdout**에 판정 결과를 표시한다. 추가 DB 조회나 AI 호출은 없다.

| 옵션 | 기본값 | 의미 |
|---|---:|---|
| `--alert-days` | 7 | 각 비교 기간의 일수. 1~3650 |
| `--alert-threshold` | 20 | 부정 비율이 이 값 이상 상승하면 경고. 단위 %p, 0 초과 100 이하 |
| `--alert-min-reviews` | 5 | 각 기간에 필요한 최소 분석 완료 리뷰 수. 양의 정수 |
| `--no-alerts` | 미지정 | 알림 판정을 생략할 때 지정 |

```bash
python main.py dashboard --product 이어폰 --date-to 2026-09-22 \
  --alert-days 7 --alert-threshold 20 --alert-min-reviews 5
```

최근 기간은 **`--date-to` 또는 실행일의 UTC 날짜**까지 양 끝 날짜를 포함한다.
위 예시의 최근 기간은 9월 16~22일, 직전 기간은 9월 9~15일이다. 분석 실행일이 아닌
**리뷰 작성일**을 사용한다. 제품 필터는 두 기간에 동일하게 적용된다.

부정 비율은 `부정 분석 건수 / 전체 분석 완료 건수`다. 미분석·실패 리뷰는 분모에서 제외한다.
20%에서 40%로 상승하면 **+20%p**이므로 기본 기준에서 경고한다. 20%에서 30%로 상승하면
+10%p이므로 경고하지 않는다. 반올림 전 비율 차이로 판정하며 경계값도 포함한다.

```text
감정 변화 확인 (각 7일, 리뷰 작성일 기준)
[경고] 부정 리뷰 비율 급증: 20.0% → 80.0% (+60.0%p, 경고 기준 +20%p)
  직전: 2026-09-09 ~ 2026-09-15 (분석 5건 / 부정 1건)
  최근: 2026-09-16 ~ 2026-09-22 (분석 5건 / 부정 4건)
```

기간별 표본이 부족하면 `[판정 보류]`로 표시한다. `--date-from`이 두 기간 중 일부를
제외하면 날짜 범위를 넓히도록 안내한다. 서비스에 감정 필터를 직접 전달한 경우에도
전체 분석 리뷰 기준 비율을 계산할 수 없어 판정을 보류한다.
경고·정상·판정 보류 모두 파일 생성이 성공하면 종료 코드는 0이다.

## 설정과 파일 보호

`visualization.font_family`와 `visualization.dpi`를 차트 생성기에 전달한다.
기본값은 빈 폰트명(설치된 한글 폰트 자동 탐색), 150 DPI다.

- 기존 파일은 기본적으로 덮어쓰지 않는다. 같은 초에 같은 디렉터리로 실행해 이름이
  겹치면 `--force`를 안내하고 종료한다. 다른 초에 실행하면 새 이름이 만들어진다.
- 차트와 리포트를 모두 임시 디렉터리에서 생성한 후 최종 경로로 게시한다.
  생성 중 오류·사용자 중단이 발생하면 임시 파일을 정리하고 기존 파일은 보존한다.
- 최종 게시에서는 파일 하나씩 완성본으로 교체한다. 두 파일 전체가 하나의 트랜잭션은
  아니다. 첫 파일 게시 후 두 번째 게시가 실패하면 오류에 이미 저장된 파일 경로를 표시한다.
  `--force`로 교체된 첫 파일은 자동 복구하지 않는다.
- `--force`가 없어도 동시 실행으로 다른 파일을 덮어쓰지 않도록 게시 시 다시 보호한다.
  출력 파일 자체가 심볼릭 링크나 디렉터리이면 `--force`에서도 거부한다.

| 종료 코드 | 의미 |
|---:|---|
| 0 | PNG·리포트 생성 성공. 빈 데이터 포함 |
| 2 | 잘못된 인자·설정 또는 Matplotlib 등 의존성 불러오기 실패 |
| 3 | 저장소·출력 디렉터리·파일 충돌·파일 저장 오류 |

## 연결과 검증

`src/runtime.py`가 명령 실행 시에만 시각화 모듈을 불러오고 `DashboardService`를 구성한다.
서비스는 `repository.get_statistics(filters)`를 한 번 호출해 같은 결과를 두 생성기에
전달하며 DB 내용을 수정하지 않는다. 저장소 연결은 런타임이 닫는다.
`DashboardRequest.alert_options`로 알림 설정을 전달하며 `None`이면 생략한다.
`DashboardResult.sentiment_change`에 판정·기간·건수·설정을 반환한다.
두 필드는 기본값을 가진 추가 필드이며 `ReviewVisualizer`, `ReportGenerator` 계약은 유지한다.

```bash
python -m unittest tests.test_dashboard_service tests.test_dashboard_cli tests.test_pipeline_runtime -v
python -m unittest tests.test_sentiment_alerts -v
python -m unittest discover -s tests -q
```

임시 SQLite와 프로젝트 복사본으로 실제 PNG·TXT/MD, 필터, 빈 통계, 다른 작업 디렉터리에서의
경로 해석, 설정 전달, 파일 충돌·실패·덮어쓰기를 검증한다. 테스트에는 API 키가 필요 없다.
