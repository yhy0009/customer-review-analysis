# 리뷰 조회 CLI

기본 실행에서 `list`, `show`, `stats`를 지원한다. 세 명령은 설정된 SQLite의
정제 리뷰(Clean)와 최신 분석 결과를 조회하며 API 키나 AI 호출을 요구하지 않는다.
조회 데이터가 없으면 빈 결과를 안내한다. DB 파일이 없으면 빈 v1 저장소를 초기화한다.

## 실행 예시

```bash
python main.py list --page 1 --size 5
python main.py list --sentiment negative --rating 2 --date-from 2026-09-01 --date-to 2026-09-30
python main.py list --product 이어폰 --sort rating --order desc
python main.py show --id 2
python main.py stats
python main.py stats --product 이어폰 --date-from 2026-09-01
python main.py --config config/query.json stats
```

`--config`와 `--log-level`은 명령 앞에 지정한다. 상대 설정·`.env`·로그·DB 경로는
프로젝트 루트 기준이며 다른 디렉터리에서 `python /프로젝트/경로/main.py stats`로
실행해도 같은 설정과 DB를 사용한다. DB는 환경변수 `CRA_DATABASE_PATH`로도 지정할 수 있다.
설정 우선순위는 CLI 명시 옵션 > 환경변수 > 설정 파일 > 기본값을 유지한다.

## 출력과 상태

- `list`: 필터링된 전체 건수와 페이지 정보를 표시한다. 각 리뷰는 제품·작성일·별점,
  본문 미리보기와 분석 결과를 표시한다. 페이지 범위를 넘으면 빈 페이지를 안내한다.
- `show`: 저장된 정제 본문 전체와 최신 감정·신뢰도·요약·키워드·모델 등 분석 정보를 표시한다.
  분석 정보가 없으면 `분석 결과 없음`을 표시한다. 이는 미분석과 실패를 구분하는 상태값이 아니다.
- `stats`: 정제 리뷰 수, 분석 완료·미분석·실패 수, 분석 완료율, 평균 별점과 감정 분포를 표시한다.
  완료율의 분모는 조건에 맞는 전체 Clean 수이며 감정 비율의 분모는 분석 완료 수다.
  미분석 수는 실패 수를 제외한다. 비율과 평균의 반올림은 출력 단계에서만 수행한다.
- 감정 필터를 지정하면 분석 결과가 있는 리뷰만 포함되므로 해당 조건의 완료율은 100%다.
- 조회 내용은 stdout으로, 사용 오류와 상세 조회 실패는 stderr로 출력한다.
  원문은 진단 로그에 기록하지 않는다. 본문의 터미널 제어 문자는 출력 시 제거한다.

| 종료 코드 | 의미 |
|---:|---|
| 0 | 조회 성공. 빈 목록·빈 통계·범위 밖 페이지 포함 |
| 1 | 요청 ID의 정제 리뷰 없음 |
| 2 | 잘못된 CLI 인자·설정 또는 아직 연결되지 않은 명령 |
| 3 | SQLite 파일·스키마·조회 오류 또는 미지원 저장소 백엔드 |

## 연결 구조

- `src/query_service.py`: 명령별 요청을 Repository 조회 메서드에 전달한다.
- `src/query_output.py`: 공통 조회 결과를 한국어 콘솔 문자열로 변환한다.
- `src/handlers.py`: 요청 생성, 결과 출력, 공통 종료 코드 처리를 담당한다.
- `src/runtime.py`: 로드된 설정으로 조회 명령을 구성한다. 요청 검증 후 저장소를 열고,
  결과를 읽은 뒤 항상 연결을 닫는다.
- `src/cli.py`: 기본 매핑을 사용하거나 호출자가 주입한 매핑을 사용한다.
  명시적으로 주입한 빈 매핑도 기본 매핑으로 대체하지 않는다.

`export`도 기본 CLI에 연결돼 있으며 [내보내기 안내](EXPORT.md)를 따른다.
`import`, `clean`, `analyze`, `extract`, `dashboard`의 기본 CLI 연결은 후속 작업이다.
기존 모듈을 이용해 적재한 SQLite DB를 조회할 수 있다. 원본만 저장되고 정제가 끝나지 않은
리뷰는 조회에 포함되지 않는다. AI 팀이 사용하는 별도 분석 핸들러 주입 방식은 유지한다.

## 검증

```bash
python -m unittest discover -s tests -p 'test_query*.py' -v
python -m unittest discover -s tests -q
```

테스트는 임시 SQLite와 별도의 프로젝트 복사본을 사용한다. 실제 `main.py`를 다른
작업 디렉터리에서 실행해 필터·정렬·페이지·상세·통계·오류와 설정 경로를 확인한다.
조회 실행은 `python -S`로도 검증하므로 외부 AI SDK가 없어도 동작한다.
