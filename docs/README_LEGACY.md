> 이 문서는 제출용 README로 개편하기 전의 개발 안내를 보관한 문서입니다. 당시의 계획·예시와 이전 실행 환경이 포함되어 있습니다. 현재 기능·설치·브랜치 기준은 [프로젝트 README](../README.md)를 참고하세요. 문서 이동에 따라 상대 링크를 조정했습니다.

# 📊 [Project C] AI 기반 고객 리뷰 감정 분석 대시보드
> **대량의 고객 리뷰 데이터를 정제·분석하여 비즈니스 의사결정 인사이트를 도출하는 CLI 기반 AI 분석 도구**

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
라이선스: 기존 README에는 MIT로 표기되어 있었으나, 이 보관 시점에 별도의 LICENSE 파일은 없습니다.

---

## 📌 1. 프로젝트 개요 (Project Overview)

고객 리뷰는 제품 및 서비스 개선을 위한 핵심 데이터이지만, 수많은 리뷰를 수동으로 분석하는 데는 많은 리소스가 소모됩니다.
본 프로젝트는 **CSV/Excel 기반 리뷰 데이터를 파이프라인을 통해 정제**하고, **AI API를 활용해 감정(긍정/중립/부정) 및 핵심 키워드/개선안을 자동 추출**한 뒤, **시각화 차트 및 종합 리포트로 인사이트를 제공하는 CLI 애플리케이션**입니다.

### 🎯 주요 기능
- **데이터 파이프라인**: CSV/Excel 로드, 텍스트 정규화, 유효성 검증 및 Raw/Clean 분리 저장 (중복 skip/upsert 지원)
- **AI 감정 분석 & 요약**: LLM API 기반 감정/신뢰도 분석, 긍·부정 빈출 키워드 및 개선 제안 자동 추출
- **CLI 데이터 조회 및 관리**: 서브커맨드 기반 페이징/필터링 조회, 통계 요약, 데이터 다중 포맷 Export (CSV/JSONL/Excel)
- **비즈니스 대시보드 시각화**: Matplotlib 기반 감정 분포, 시간별 추이, 별점-감정 매트릭스 차트(PNG), 종합 리포트(TXT/MD), 차트·통계를 내장한 단일 HTML 생성

---

## 👥 2. 팀원 구성 및 역할 배분 (Team Roles)

| 팀원 (GitHub ID) | 담당 파트 | 세부 업무 내용 |
| :--- | :--- | :--- |
| **`yhy0009`** | **CLI & 공통 인프라 / I/O** | • `argparse` 기반 서브커맨드 CLI 인터페이스 설계<br>• 설정 파일(`config.json`) 및 로깅(`logging`) 시스템 구축<br>• 데이터 조회(`list`, `show`, `stats`) 및 검색 기능 구현<br>• 데이터 내보내기(`export`) 다중 포맷(CSV/JSONL/Excel) 개발 |
| **`sayknow`** | **데이터 파이프라인 & Python 시각화** | • CSV/Excel 리뷰 데이터 수집 및 Raw 저장소 적재(`import`)<br>• 데이터 정제 규칙 적용 및 중복 처리(Clean 저장소 분리, `clean`)<br>• Matplotlib 기반 대시보드 차트 3종 생성(`dashboard`)<br>• 테스트용 샘플 리뷰 데이터셋 구성 (30건 이상) |
| **`highslow1536`** | **AI 모델링 & 리포팅 & JS 대시보드** | • AI API 연동 및 감정 분류(긍정/부정/중립)·점수 분석(`analyze`)<br>• AI 기반 조건별 빈출 키워드/이슈 요약 및 개선안 추출(`extract`)<br>• 품질 지표 & TOP N 집계 기반 종합 리포트 생성 기능 개발<br>• JS 웹 대시보드 및 Python 산출물 연결 |

---

## 🛠 3. 기술 스택 (Tech Stack)

- **언어**: Python 3.10+
- **데이터 저장소**: SQLite3 / JSONL (영구 저장소)
- **데이터 분석 & 시각화**: `matplotlib`, `pandas`, `openpyxl`
- **AI 연동**: OpenAI API / Anthropic API / Google Gemini API 등 (공식 SDK 또는 `requests`)
- **CLI & 유틸리티**: `argparse`, `logging`

---

## 🌲 4. Git 브랜치 전략 (Git Branch Strategy)

본 프로젝트는 **GitHub Flow**를 기본으로 하되 안정적인 릴리즈를 위해 기능 단위 브랜칭을 엄격히 적용합니다.

```text
main (배포 및 제출용 안정화 브랜치)
 └── develop (통합 개발 브랜치)
      ├── feat/cli-design (yhy0009)
      ├── feat/data-pipeline (sayknow)
      ├── feat/ai-sentiment (highslow1536)
      └── ...

```

### 브랜치 명명 규칙

* `develop`: 팀원들의 작업 결과물이 1차 병합되는 기본 작업 브랜치
* `feat/<기능명>`: 새로운 기능 개발 (예: `feat/cli-subcommands`, `feat/data-cleaner`, `feat/sentiment-analysis`)
* `fix/<버그명>`: 버그 수정 (예: `fix/sqlite-upsert-error`)
* `refactor/<대상>`: 코드 리팩토링 및 모듈화
* `docs/<내용>`: 문서 작성 및 수정 (예: `docs/readme-update`)

### 협업 워크플로우 (PR & Code Review)

1. `develop` 브랜치에서 분기하여 기능 브랜치 생성: `git checkout -b feat/feature-name`
2. 단위 기능 개발 후 커밋 (커밋 컨벤션 준수)
3. 원격 저장소에 Push 후 `develop` 브랜치를 향해 **Pull Request(PR)** 생성
4. **최소 1명 이상의 팀원 리뷰 및 승인(Approve)** 후 `develop`에 Squash & Merge
5. 최종 과제 완성 시 `develop` → `main` 병합

---

## 💬 5. 커밋 메시지 컨벤션 (Commit Convention)

**Conventional Commits** 표준 규격을 따릅니다.

### 커밋 형식

```text
<type>(<scope>): <subject>

[선택사항: 본문(body)]
[선택사항: 이슈 번호(footer)]

```

### Type 목록

| Type | 설명 |
| --- | --- |
| **`feat`** | 새로운 기능 추가 |
| **`fix`** | 버그 및 오류 수정 |
| **`docs`** | 문서 수정 (README.md, 주석 등) |
| **`style`** | 코드 포맷팅, 세미콜론 누락 등 (비즈니스 로직 영향 없음) |
| **`refactor`** | 코드 리팩토링 (기능 변경 없는 구조 개선) |
| **`test`** | 테스트 코드 작성 및 수정 |
| **`chore`** | 빌드 스크립트 수정, 패키지 매니저 설정, `.gitignore` 수정 등 |

### 커밋 예시

* `feat(cli): argparse 기반 서브커맨드 9종 기본 골격 구현`
* `feat(clean): 리뷰 텍스트 정규화 및 결측치 필터링 로직 추가`
* `feat(ai): 감정 분석 API 연동 및 신뢰도 점수 파싱 구현`
* `fix(storage): sqlite upsert 시 중복 키 충돌 예외 처리`

---

## 📂 6. 프로젝트 구조 (Directory Structure)

```text
├── config/
│   ├── config.json          # 설정 파일 (API 키, 경로, 중복 정책 등)
│   └── config_example.json  # 설정 예시 템플릿
├── data/
│   ├── sample_reviews.csv   # 테스트용 샘플 데이터 (30건 이상)
│   └── app_database.db      # SQLite 영구 저장소 (raw/clean 테이블)
├── output/                  # 생성된 차트 이미지 및 리포트 파일
│   ├── dashboard_20260922_010203.png  # 차트 3종과 키워드를 담은 통합 이미지
│   ├── dashboard_20260922_010203.html # --html 지정 시 생성하는 독립 실행 HTML
│   └── report_20260922_010203.md
├── src/
│   ├── __init__.py
│   ├── cli.py               # argparse 서브커맨드 핸들러
│   ├── config.py            # 설정 및 로깅(logging) 초기화
│   ├── errors.py            # 공통 애플리케이션 예외 계층
│   ├── models.py            # 모듈 경계용 공통 enum/dataclass
│   ├── services.py          # 기능 모듈 및 애플리케이션 서비스 Protocol
│   ├── handlers.py          # CLI 인자 → 요청 객체 변환 및 서비스 연결
│   ├── runtime.py           # 기본 CLI 명령 구성과 저장소 연결 수명 관리
│   ├── query_service.py     # 목록·상세·통계 조회 서비스
│   ├── query_output.py      # 조회·인사이트 결과 콘솔 표시
│   ├── export_service.py    # 조건에 맞는 전체 리뷰 조회와 내보내기 조율
│   ├── collector.py         # 데이터 수집 (CSV/Excel 로더)
│   ├── cleaner.py           # 데이터 정제 및 유효성 검증
│   ├── storage.py           # SQLite/JSONL 영구 저장소 관리 모듈
│   ├── analyzer.py          # AI 감정 분석 및 키워드/요약 추출
│   ├── visualizer.py        # Matplotlib 대시보드 차트 시각화
│   ├── dashboard_service.py # 저장된 통계로 차트·리포트 생성 조율
│   ├── html_dashboard.py    # PNG·통계·감정 변화 알림을 내장한 HTML 생성
│   ├── reporter.py          # 종합 리포트 생성기
│   └── exporter.py          # CSV/JSONL/Excel 데이터 내보내기
├── tests/                   # 단위 테스트
├── .env.example             # 환경변수 템플릿
├── .gitignore
├── requirements.txt         # 의존성 패키지 목록
├── main.py                  # CLI 엔트리포인트 실행 파일
└── README.md

```

---

## 🚀 7. 시작하기 (Quick Start)

### 1) 환경 설정 및 가상환경 생성

```bash
# 레포지토리 클론
git clone [https://github.com/](https://github.com/)<your-org-or-user>/customer-review-sentiment-dashboard.git
cd customer-review-sentiment-dashboard

# Python 3.10+ 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 필수 패키지 설치
pip install -r requirements.txt

```

### 2) API 키 및 설정 파일 구성

```bash
# 환경변수 파일 생성 및 API 키 설정
cp .env.example .env

# config.json 확인 및 수정
cp config/config_example.json config/config.json

```

---

## 💻 8. CLI 사용법 (Usage Guide)

현재 `main.py`의 기본 실행은 **`import`, `clean`, `analyze`, `extract`, `list`, `show`, `stats`, `dashboard`, `export`, `compare`** 10개 명령을 지원합니다.
CSV/Excel을 SQLite 원본 저장소에 적재·정제하고, 정제 리뷰의 분석·인사이트 추출·조회·차트 및 리포트 생성·내보내기를 수행합니다.
`dashboard`는 API 호출 없이 저장된 통계로 PNG와 TXT/Markdown 리포트를 생성합니다.
`--html`을 추가하면 차트·통계·조회 조건·감정 변화 알림을 담은 HTML도 생성합니다. HTML 파일 하나만 복사해 브라우저에서 열 수 있습니다.
저장된 AI 인사이트가 있으면 웹 화면처럼 요약 카드, 주요 이슈·개선 제안, 펼쳐 보는 제품별 원문 근거도 포함합니다.
실행 시 최근 7일과 직전 7일의 부정 비율을 비교해 20%p 이상 상승하면 경고합니다(기간별 분석 최소 5건).
필터·출력 경로·덮어쓰기 사용법은 [대시보드 CLI 안내](DASHBOARD.md)를 참고하세요.
`import`의 입력 형식과 중복 정책은 [원본 리뷰 적재 안내](IMPORT.md)를 참고하세요.
`clean`의 정제 기준·재실행 정책과 제외 상태 처리는 [리뷰 정제 안내](CLEAN.md)를 참고하세요.
원본만 적재한 리뷰는 정제 전까지 조회·통계·AI 분석 대상에 포함되지 않습니다.
세 명령의 공통 결과 출력과 서비스 주입용 등록 경계는 준비되어 있습니다.
기능 담당자의 서비스 연결 방법은 [수집·정제·대시보드 CLI 연결 안내](CLI_PIPELINE_INTEGRATION.md)를 참고하세요.
조회 필터와 경로 규칙은 [조회 CLI 안내](QUERY_CLI.md), 파일 포맷과 덮어쓰기 규칙은
[내보내기 안내](EXPORT.md)를 참고하세요.
분석·추출 명령의 설정, 출력과 종료 코드는 [AI CLI 실행 안내](AI_CLI.md)에 있습니다.

> 구현 현황: GPT-5-mini 단건 분석과 저장소 주입 방식의 배치 분석·재시도를 제공합니다.
> [AI 분석 실행·테스트 안내](AI_ANALYSIS.md)를 참고하세요.
> 한국어·영어·한영 혼합 리뷰를 원문으로 분석하고 한국어 요약·키워드를 생성합니다.
> [다국어 감정 분석 안내](MULTILINGUAL_SENTIMENT.md)에 샘플 입력과 언어별 평가 방법이 있습니다.
> 조건별 키워드 집계와 AI 이슈·개선안 요약 서비스도 제공합니다.
> 대상 선택·입력 한도·서비스 연결 방법은 [인사이트 추출 안내](INSIGHT_EXTRACTION.md)에 있습니다.
> 통계와 선택적 인사이트를 TXT·Markdown으로 저장하는 [종합 리포트 생성기](REPORT_GENERATION.md)를 제공합니다.
> 고정 합성 리뷰의 분류 지표와 서비스 전체 흐름을 검증하는 [AI 평가 도구](AI_EVALUATION.md)도 제공합니다.
> 대량 리뷰는 근거 배치와 전체 인용 목록으로 처리하며, 저장 평가의 [정책 기반 품질 판정](AI_QUALITY_GATE.md)을 제공합니다.
> 저장소의 기존 import 경로와 구형 DB 처리 방법은 [SQLite 저장소 안내](SQLITE_STORAGE.md)에 있습니다.
> `data/sample_reviews.csv`에 합성 리뷰 40행이 준비되어 있습니다. 중복 2행과 정제 제외 4행을 포함하며,
> 선택 항목 누락 4건을 포함한 34건이 정제됩니다. [최종 테스트 플로우](FINAL_TEST_FLOW.md)에서
> 새 DB 준비, 예상 건수, 실제 AI 분석과 인사이트 리포트 확인 순서를 안내합니다.

```bash
# 1. 리뷰 데이터 가져오기 (Raw 적재)
python main.py import --file data/reviews.csv

# 2. 데이터 정제 (Clean 적재)
python main.py clean --policy skip

# 3. AI 감정 분석 실행
python main.py analyze --unanalyzed --limit 20

# 4. AI 키워드 및 인사이트 요약 추출·JSON 자동 저장
python main.py extract --sentiment negative --limit 50

# 5. 리뷰 목록 및 상세 조회
python main.py list --sentiment negative --page 1 --size 5
python main.py show --id 102

# 6. 전체 통계 요약 확인
python main.py stats

# 7. 저장된 분석 통계와 유효한 추출 결과로 PNG + Markdown 생성 (API 호출 없음)
python main.py dashboard --output output/ --report-format md

# 단일 HTML 대시보드도 함께 생성
python main.py dashboard --output output/ --html

# 8. 데이터 내보내기 (Export)
python main.py export --format csv --sentiment negative --rating-min 3 --output output/negative_reviews.csv

```

---

## 🌐 JS 웹 대시보드

```bash
# 합성 리뷰와 저장된 분석 결과로 실행 (AI 호출 없음)
python scripts/serve_dashboard.py --demo

# 실제 SQLite를 읽기 전용으로 조회
python scripts/serve_dashboard.py --database data/app_database.db
```

브라우저에서 `http://127.0.0.1:8765`에 접속합니다. Node 설치나 프론트엔드 빌드 없이
제품·기간·감정·별점 필터, 통계, 리뷰 상세, 원문 근거 탐색, TXT/MD 리포트 다운로드를 제공합니다.
리뷰 탐색에서는 현재 조건 전체 리뷰를 CSV·JSONL로 다운로드할 수 있습니다(최대 2,000건).
조회 조건과 페이지를 URL로 유지해 새로고침·뒤로가기 후에도 복원합니다.
`sayknow`의 Python 차트 생성기를 `highslow1536`의 JS 화면에서 표시합니다.
AI 인사이트는 별도로 준비한 파일을 연결하며 페이지 조회로 AI를 호출하지 않습니다.
화면에서 현재 조건의 인사이트를 생성·저장하려면 실제 DB 실행 명령에 `--enable-insights`를
추가합니다. 버튼을 눌렀을 때만 AI를 호출하고, 조건별 결과를 저장해 재사용합니다.
요청 모델과 프롬프트 버전을 화면에 표시하며, 서버 재시작 후 생성 설정이 바뀌면 이전 결과의
재생성을 요구합니다. API 키 교체만으로는 기존 결과를 무효화하지 않습니다.
실행·데이터 계약·검증 방법은 [웹 대시보드 안내](WEB_DASHBOARD.md)를 참고하세요.

---

## 📋 9. 개발 규약 및 체크리스트 (Ground Rules)

* [ ] **API 키 하드코딩 금지**: `.env` 또는 `config.json`을 통해서만 로드하며 Git에 노출하지 않습니다.
* [ ] **영구 저장소 원칙**: 메모리 관리가 아닌 SQLite / JSONL 영구 저장을 거쳐야 합니다.
* [ ] **모듈화 준수**: 단일 파일 집중 개발을 지양하고 최소 4개 이상의 책임별 모듈로 분리합니다.
* [ ] **예외 처리 및 로깅**: AI API 실패 또는 파일 형식 오류 시 적절한 에러 로그(`logging`)를 남기고 프로세스가 비정상 종료되지 않도록 처리합니다.

---

## 📊 10. 제품·카테고리별 비교 분석

추가 명령 `compare`로 저장된 리뷰의 제품·카테고리별 건수, 평균 별점, 감정 비율과 분석
완료율을 비교할 수 있습니다. API 호출 없이 CLI 표·CSV·JSON과 선택적 PNG 차트를 생성합니다.

```bash
python main.py compare --name "이어폰 A" --name "이어폰 B"
python main.py compare --group-by category --output output/comparison --chart
```

카테고리는 CSV·Excel의 선택 열 `category`, `product_category`, `카테고리`, `상품분류`를
사용합니다. 값이 없는 리뷰는 별도 그룹으로 포함하며 분석 0건의 감정 비율은 `N/A`입니다.
입력 예시·지표 기준·옵션·파일 보호 규칙은 [비교 분석 안내](COMPARISON.md)를 참고하세요.
