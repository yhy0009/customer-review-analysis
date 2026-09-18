# 수집·정제·대시보드 CLI 연결

## 현재 제공하는 범위

`import`, `clean`, `dashboard`에 공통 결과 출력 어댑터와 서비스 주입 방식의
CLI 등록 경계를 제공한다. `ImportService`는 수집기와 원본 저장소를 연결한다.
`CleanService`는 저장된 원본을 정제하고 성공·제외 상태를 저장한다.
차트·리포트 생성 모듈을 조율하는 대시보드 서비스는 후속 작업이다.
기존 `ApplicationServices`, Request, Result 계약은 유지한다.

**기본 CLI는 import/clean/analyze/extract/list/show/stats/export 8개를 실행한다.**
`import`는 기본 생성 함수로 `collector.load_reviews`와 `ImportService`를 구성한다.
사용법과 중복 정책은 [원본 리뷰 적재 안내](IMPORT.md)를 따른다.
`clean`은 `cleaner.clean_reviews`와 `CleanService`를 기본 연결한다([리뷰 정제 안내](CLEAN.md)).
`dashboard`는 실제 서비스 생성 함수를 등록하기 전까지 미연결 오류(종료 코드 2)를 반환한다.
서비스 준비가 끝난 명령부터 개별 등록할 수 있다.

## 서비스 등록

`src.runtime.build_default_handlers()`는 다음 선택적 키워드 인자를 받는다.

| 인자 | 생성 함수가 반환할 호출 대상 | 반환 결과 |
|---|---|---|
| `import_factory` | `import_reviews(ImportRequest)` | `BatchOperationResult` |
| `clean_factory` | `clean_reviews(CleanRequest)` | `CleanBatchResult` |
| `dashboard_factory` | `create_dashboard(DashboardRequest)` | `DashboardResult` |

생성 함수의 형태는 `factory(repository, config) -> service_method`다.
`repository`는 이번 명령에서 사용할 열린 SQLite 저장소이며, `config`는 환경변수와
설정 파일 우선순위가 적용된 전체 설정이다. 반환값은 요청 객체 하나를 받는 함수 또는
서비스의 바인딩된 메서드다. 전체 `ApplicationServices` 객체를 만들 필요는 없다.

아래 함수는 기본 import 서비스를 교체하고 싶을 때 호출자가 제공하는 `make_service(repository, config)`로 서비스를
구성한다. `make_service`는 실제 `import_reviews()` 메서드를 가진 객체를 반환해야 한다.
이 예시 자체는 수집·저장 로직을 구현하지 않는다.

```python
from src.cli import main
from src.runtime import build_default_handlers


def run_import_cli(argv, make_service):
    def import_factory(repository, config):
        service = make_service(repository, config)
        return service.import_reviews

    handlers = build_default_handlers(import_factory=import_factory)
    return main(argv, handlers=handlers)
```

동일한 방식으로 `clean_factory`, `dashboard_factory`를 함께 전달할 수 있다.
`import_factory`·`clean_factory`를 생략하면 해당 기본 서비스가 등록되고, 전달하면 교체된다.
`dashboard_factory`는 생략하면 등록되지 않는다.
어느 생성 함수든 명시적으로 `None`을 전달하면 해당 파이프라인 명령을 등록하지 않는다.
기존 analyze/extract/list/show/stats/export 6개 명령은 매핑에 유지된다.
구체 서비스와 선택적 패키지의 import는 생성 함수 내부에서 수행해, 도움말이나 다른
명령을 구성하는 것만으로 pandas·matplotlib·AI SDK가 필요해지지 않도록 한다.

## 실행과 저장소 수명

1. 공통 CLI가 환경 파일과 설정을 로드한다.
2. 핸들러가 CLI 인자를 요청 객체로 변환하고 검증한다.
3. 런타임이 설정에 지정된 SQLite 연결을 연다.
4. 해당 명령의 생성 함수를 호출한 뒤 반환된 서비스 메서드에 요청을 전달한다.
5. 성공·오류·사용자 중단 모두 연결을 닫는다. 성공적으로 반환된 결과는 핸들러가 출력한다.

생성 함수는 명령을 실행할 때마다 호출된다. 서비스는 전달받은 저장소를 빌려 사용하며
직접 닫거나 전역에 보관하지 않는다. 저장·배치·트랜잭션 동작은 기존 Repository 계약을
따른다. 런타임이 명령 전체를 하나의 쓰기 트랜잭션으로 감싸지는 않는다.
`CleanService`는 원본 한 건마다 정제·저장을 수행한다. 행 오류는 결과에 집계하고,
DB 장애는 중단·전파한다. 앞서 저장된 성공·제외 결과는 유지된다.

- ImportRequest에는 프로젝트 루트 기준 절대 입력 경로와 중복 정책이 들어 있다.
- CleanRequest.options에는 CLI/설정에서 결정된 중복 정책과 최소 길이가 들어 있다.
- DashboardRequest에는 필터, 절대 출력 경로, 리포트 형식과 force가 들어 있다.
  시각화 모듈 구성에는 생성 함수가 `config["visualization"]`의 필요한 값을 전달한다.
- 요청 검증 실패 시 DB나 서비스 생성 함수를 호출하지 않는다.
- 서비스 생성 또는 실행 중 ImportError가 발생하면 의존성 확인 안내와 코드 2를 반환한다.
- API 호출, 정제 제외 상태 저장, 차트와 리포트 조율은 구체 서비스의 책임이다.

## 결과 출력

`src.handlers`의 `build_import_handler`, `build_clean_handler`,
`build_dashboard_handler`를 개별 사용할 수도 있다. 이 방식에서는 저장소 수명을
호출자가 관리한다. 기존 `build_handlers(services)`도 세 출력 어댑터를 사용한다.

수집·정제는 처리 결과를 stdout에 표시한다. 예를 들어 정제 결과는 다음과 같다.

```text
processed=4 succeeded=2 skipped=0 failed=1 rejected=1
  [INVALID_REVIEW_DATE] item=3: 날짜를 확인하세요.
  [CLEANING_ERROR] item=4: 정제 처리에 실패했습니다.
```

`failed`와 `rejected`를 구분하고, 빈 결과와 모든 항목이 건너뛰어진 결과도 건수를 표시한다.
실패 상세는 반환된 `ItemError`의 식별자·코드·메시지를 사용한다. 서비스는 이 필드에
API 키나 원문 전체를 넣지 않아야 한다. 터미널 제어 문자는 제거하고 줄바꿈은 합친다.

대시보드는 `DashboardResult.artifacts`에 실제로 반환된 파일의 종류·포맷·절대 경로를
표시한다. 목록이 비었으면 `생성된 파일이 없습니다.`를 표시한다. 출력 어댑터는 파일을
생성하거나 경로를 추측하지 않는다. 진단 로그와 명령 오류 안내는 stderr로 출력한다.

| 종료 코드 | 의미 |
|---:|---|
| 0 | 정상 반환. 빈 결과·중복 건너뛰기·빈 산출물 목록 포함 |
| 1 | 수집·정제 결과에 failed 또는 rejected가 존재 |
| 2 | 요청·설정·미연결·의존성 불러오기 오류 |
| 3 | 입력 파일·저장소·파일 출력 오류 |
| 4 | AI 제공자 오류 |

## 검증

```bash
python -m unittest tests.test_pipeline_handlers tests.test_pipeline_runtime -v
python -m unittest tests.test_import_service tests.test_import_cli -v
python -m unittest tests.test_clean_service tests.test_clean_cli tests.test_clean_rejection_storage -v
python -m unittest discover -s tests -q
```

테스트는 결과 출력, 필터·옵션 전달, 명령별 선택 등록, DB 생성 전 검증, 실제 SQLite 저장,
성공·실패·중단 시 연결 종료와 다른 작업 디렉터리에서의 경로 해석을 확인한다.
`python -S` 프로세스에서도 서비스 대역을 주입해 선택적 패키지 없이 연결 경계가 동작하는지
검증한다. import 테스트는 실제 CSV/Excel과 SQLite를 사용해 기본 연결·중복 정책·오류를 확인한다.
정제 테스트는 저장 결과 집계·중복 정책·제외 상태·재시도와 import부터 조회까지 확인한다.
API를 호출하지 않으며, 대시보드 CLI 서비스의 완료 검증은 후속 작업이다.
