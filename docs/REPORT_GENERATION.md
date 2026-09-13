# 종합 리포트 생성

`src/reporter.py`의 `FileReportGenerator`는 공통 `ReportGenerator` Protocol을 구현한다.
저장소에서 받은 `ReviewStatistics`와 선택적인 `InsightResult`를 UTF-8 TXT 또는
Markdown 파일로 쓰고 `OutputArtifact(kind=REPORT, path=절대경로, format="txt"|"md")`를 반환한다.
리포터는 저장소 조회, AI 호출, 차트 생성, 콘솔 출력을 수행하지 않는다.

## 포함 내용과 집계 기준

1. UTC 리포트 생성 시각
2. 정제·분석 완료·미분석·실패 건수, 분석 완료율·실패율, 평균 별점
3. 분석 완료 리뷰 기준 감정별 건수·비율
4. 리뷰 작성일별 감정 건수와 별점 1~5별 감정 분포
5. 통계 대상 긍·부정 TOP N 키워드
6. 제공된 인사이트의 추출 조건·실제 대상 수·생성 시각·요약·이슈·개선 제안 및 TOP N 키워드

통계는 `repository.get_statistics()`의 집계값을 사용한다. 리포터는 원본 리뷰를
다시 세거나 키워드를 재집계하지 않는다. 완료율·실패율은 정제 리뷰 수를 분모로 표시하고,
감정 비율은 저장소가 계산한 분석 완료 리뷰 기준 비율을 표시한다. 비율은 소수점 한 자리,
평균 별점은 소수점 두 자리로 표시하며 입력 DTO는 변경하지 않는다.
이 지표들은 데이터 처리 현황을 나타내며 감정 분류 정확도의 평가값이 아니다.

TOP N은 기본 10이며 `FileReportGenerator(top_n=5)`처럼 표시 개수를 바꿀 수 있다.
전달된 순위를 유지해 앞에서 N개만 표시한다. 저장소나 추출기가 10개를 전달했다면
N을 더 크게 설정해도 추가 키워드를 조회하지 않는다.

`ReviewStatistics`에는 조회 조건과 스냅샷 시각이 포함되지 않으므로 리포터가
인사이트와 동일한 모집단인지 판별할 수 없다. 통계와 인사이트는 별도 영역으로 표시하고,
인사이트에는 자체 필터와 실제 대상 수를 명시한다. 리포트 생성 시각도 DB 조회 시각과
구분한다. 같은 조건·시점의 비교가 필요하면 호출자가 해당 데이터의 일관성을 보장해야 한다.

## 빈 결과와 텍스트 처리

- 통계가 비어도 파일을 생성한다. 평균은 `N/A`, 완료율·실패율은 `0.0%`로 표시한다.
- `insight=None`이면 인사이트 미제공을 표시한다. 대상이 0건인 인사이트는 AI 요약을
  생성하지 않았다고 표시하고, 본문에 남아 있을 수 있는 요약·제안을 표시하지 않는다.
- 제품명·키워드·AI 문장의 줄바꿈은 공백으로 합친다. 본문 길이를 임의로 자르지 않는다.
- Markdown에서는 외부 텍스트를 일반 문자로 이스케이프해 링크·이미지·HTML·제목이나
  표의 새 셀이 되지 않도록 한다. TXT에서는 문자를 유지하며 제어 문자를 제거한다.

## 파일 경로와 덮어쓰기

- 확장자 없는 경로 또는 이미 존재하는 디렉터리에는 `report_YYYYMMDD_HHMMSS.txt|md`를
  UTC 시각으로 생성한다. 점이 들어간 새 디렉터리는 미리 생성한 후 전달한다.
- `.txt`·`.md` 경로로 파일명을 직접 지정할 수 있으며 `report_format`과 일치해야 한다.
- 상대 경로는 프로젝트 루트 기준이다. 필요한 상위 디렉터리는 생성한다.
- 기존 파일은 기본적으로 보호하며 `force=True`로만 교체한다. 같은 초의 자동 파일명이
  충돌해도 이 규칙을 따른다. 출력 대상 자체가 심볼릭 링크라면 거부한다.
- 같은 디렉터리의 임시 파일에 작성을 마친 후 최종 파일로 게시한다. 작성이나 게시가
  실패하면 `OutputError`를 반환하고 기존 파일을 보존하며 임시 파일을 정리한다.
- 잘못된 옵션이나 파일 확장자는 `ValidationError`다. 파일 I/O 예외의 원문은 노출하지 않는다.

## dashboard 담당자 연결 예시

```python
from pathlib import Path
from src.config import resolve_project_path
from src.models import ReportFormat, ReviewFilter
from src.reporter import FileReportGenerator
from src.storage import SQLiteReviewRepository

filters = ReviewFilter(product_name="이어폰")
with SQLiteReviewRepository(resolve_project_path("data/app_database.db")) as repository:
    statistics = repository.get_statistics(filters)

# extract에서 얻은 InsightResult가 있다면 None 대신 전달한다.
artifact = FileReportGenerator().generate_report(
    statistics,
    None,
    Path("output/reports"),
    report_format=ReportFormat.MARKDOWN,
    force=False,
)
# dashboard 조율자가 artifact를 DashboardResult.artifacts에 포함한다.
```

`dashboard` 핸들러 조율과 기본 CLI 등록은 별도 통합 작업이다.
이 후속 브랜치는 extract PR #12 위에서 분기했으며, 리포터 본체는 공통 모델만 소비한다.
SQLite → extract → reporter를 검증하는 통합 테스트는 PR #12 구현을 사용한다.

## 검증

```bash
python -m unittest discover -s tests -p 'test_reporter.py' -v
python -m unittest discover -s tests -v
```

리포터 테스트는 두 포맷의 실제 파일 내용, 빈 데이터, TOP N, 인사이트 대상 구분,
Markdown 이스케이프, 경로·덮어쓰기·동시 생성·실패 시 파일 보존과 임시 파일 정리,
SQLite 통계 및 extract 결과 소비를 검증한다. 리포트 생성에는 API 요청이나 키가 필요 없다.
