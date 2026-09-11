# 📊 [Project C] AI 기반 고객 리뷰 감정 분석 대시보드
> **대량의 고객 리뷰 데이터를 정제·분석하여 비즈니스 의사결정 인사이트를 도출하는 CLI 기반 AI 분석 도구**

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 📌 1. 프로젝트 개요 (Project Overview)

고객 리뷰는 제품 및 서비스 개선을 위한 핵심 데이터이지만, 수많은 리뷰를 수동으로 분석하는 데는 많은 리소스가 소모됩니다.  
본 프로젝트는 **CSV/Excel 기반 리뷰 데이터를 파이프라인을 통해 정제**하고, **AI API를 활용해 감정(긍정/중립/부정) 및 핵심 키워드/개선안을 자동 추출**한 뒤, **시각화 차트 및 종합 리포트로 인사이트를 제공하는 CLI 애플리케이션**입니다.

### 🎯 주요 기능
- **데이터 파이프라인**: CSV/Excel 로드, 텍스트 정규화, 유효성 검증 및 Raw/Clean 분리 저장 (중복 skip/upsert 지원)
- **AI 감정 분석 & 요약**: LLM API 기반 감정/신뢰도 분석, 긍·부정 빈출 키워드 및 개선 제안 자동 추출
- **CLI 데이터 조회 및 관리**: 서브커맨드 기반 페이징/필터링 조회, 통계 요약, 데이터 다중 포맷 Export (CSV/JSONL/Excel)
- **비즈니스 대시보드 시각화**: Matplotlib 기반 감정 분포, 시간별 추이, 별점-감정 매트릭스 차트(PNG) 및 종합 리포트(TXT/MD) 생성

---

## 👥 2. 팀원 구성 및 역할 배분 (Team Roles)

| 팀원 (GitHub ID) | 담당 파트 | 세부 업무 내용 |
| :--- | :--- | :--- |
| **`yhy0009`** | **CLI & 공통 인프라 / I/O** | • `argparse` 기반 서브커맨드 CLI 인터페이스 설계<br>• 설정 파일(`config.json`) 및 로깅(`logging`) 시스템 구축<br>• 데이터 조회(`list`, `show`, `stats`) 및 검색 기능 구현<br>• 데이터 내보내기(`export`) 다중 포맷(CSV/JSONL/Excel) 개발 |
| **`sayknow`** | **데이터 파이프라인 & 시각화** | • CSV/Excel 리뷰 데이터 수집 및 Raw 저장소 적재(`import`)<br>• 데이터 정제 규칙 적용 및 중복 처리(Clean 저장소 분리, `clean`)<br>• Matplotlib 기반 대시보드 차트 3종 생성(`dashboard`)<br>• 테스트용 샘플 리뷰 데이터셋 구성 (30건 이상) |
| **`highslow1536`** | **AI 모델링 & 리포팅** | • AI API 연동 및 감정 분류(긍정/부정/중립)·점수 분석(`analyze`)<br>• AI 기반 조건별 빈출 키워드/이슈 요약 및 개선안 추출(`extract`)<br>• 품질 지표 & TOP N 집계 기반 종합 리포트 생성 기능 개발 |

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
│   ├── sentiment_distribution.png
│   ├── sentiment_trend.png
│   ├── rating_sentiment_matrix.png
│   └── report_20260110.md
├── src/
│   ├── __init__.py
│   ├── cli.py               # argparse 서브커맨드 핸들러
│   ├── config.py            # 설정 및 로깅(logging) 초기화
│   ├── errors.py            # 공통 애플리케이션 예외 계층
│   ├── models.py            # 모듈 경계용 공통 enum/dataclass
│   ├── services.py          # 기능 모듈 및 애플리케이션 서비스 Protocol
│   ├── handlers.py          # CLI 인자 → 요청 객체 변환 및 서비스 연결
│   ├── collector.py         # 데이터 수집 (CSV/Excel 로더)
│   ├── cleaner.py           # 데이터 정제 및 유효성 검증
│   ├── storage.py           # SQLite/JSONL 영구 저장소 관리 모듈
│   ├── analyzer.py          # AI 감정 분석 및 키워드/요약 추출
│   ├── visualizer.py        # Matplotlib 대시보드 차트 시각화
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

> 구현 현황: GPT-5-mini 단건 분석과 저장소 주입 방식의 배치 분석·재시도를 제공합니다.
> [AI 분석 실행·테스트 안내](docs/AI_ANALYSIS.md)를 참고하세요.
> 저장소의 기존 import 경로와 구형 DB 처리 방법은 [SQLite 저장소 안내](docs/SQLITE_STORAGE.md)에 있습니다.
> 아래 CLI 예시는 목표 사용법입니다. 분석 전용 CLI 어댑터는 구현됐으며,
> `main.py`의 기본 저장소·서비스 생성 연결은 아직 후속 작업입니다.

```bash
# 1. 리뷰 데이터 가져오기 (Raw 적재)
python main.py import --file data/sample_reviews.csv

# 2. 데이터 정제 (Clean 적재)
python main.py clean --policy skip

# 3. AI 감정 분석 실행
python main.py analyze --unanalyzed --limit 20

# 4. AI 키워드 및 인사이트 요약 추출
python main.py extract --sentiment negative --limit 50

# 5. 리뷰 목록 및 상세 조회
python main.py list --sentiment negative --page 1 --size 5
python main.py show --id 102

# 6. 전체 통계 요약 확인
python main.py stats

# 7. 시각화 대시보드 차트 생성
python main.py dashboard --output output/

# 8. 데이터 내보내기 (Export)
python main.py export --format csv --sentiment negative --rating-min 3 --output output/negative_reviews.csv

```

---

## 📋 9. 개발 규약 및 체크리스트 (Ground Rules)

* [ ] **API 키 하드코딩 금지**: `.env` 또는 `config.json`을 통해서만 로드하며 Git에 노출하지 않습니다.
* [ ] **영구 저장소 원칙**: 메모리 관리가 아닌 SQLite / JSONL 영구 저장을 거쳐야 합니다.
* [ ] **모듈화 준수**: 단일 파일 집중 개발을 지양하고 최소 4개 이상의 책임별 모듈로 분리합니다.
* [ ] **예외 처리 및 로깅**: AI API 실패 또는 파일 형식 오류 시 적절한 에러 로그(`logging`)를 남기고 프로세스가 비정상 종료되지 않도록 처리합니다.
