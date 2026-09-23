# AI 품질 및 서비스 통합 평가

`scripts/evaluate_ai.py`는 고정된 합성 리뷰를 이용해 SQLite → 분석 → 인사이트 추출 →
TXT·Markdown 리포트 생성까지 검증한다. 기본 CLI 등록과 별개의 개발용 도구다.

## 평가 데이터

`evaluation/reviews.v1.json`에는 긍정·중립·부정 각 6건, 총 18건이 있다. 명확한 감정,
평가 없는 사실, 균형 잡힌 혼합 감정, 반어, 부정 표현, 오타, 별점과 본문의 불일치,
리뷰 안의 출력 변경 지시를 포함한다. 각 사례에 ID·유형·기대 라벨·판단 근거를 고정했다.

데이터와 라벨은 AI가 작성한 **개발용 초안**이다. 서비스 사용자 분포를 대표하거나
사람이 검증한 독립 시험 세트가 아니다. 이 세트로 프롬프트를 조정하면 별도 보류 세트로
다시 평가해야 한다. 실행 결과에는 원본 파일 SHA-256을 남겨 버전이 같아도 내용이 달라지면
알 수 있도록 한다. 기대 라벨·판단 근거·유형은 모델 요청에 포함하지 않는다.

정량 지표와 사람의 판단을 함께 사용하고, 정상·경계·지시문 사례를 포함하는 구성은
[OpenAI 평가 가이드](https://developers.openai.com/api/docs/guides/evaluation-best-practices)를 참고했다.

## 실행

```bash
# 데이터 형식만 확인. 설정·키·DB·네트워크 없이 실행한다.
python scripts/evaluate_ai.py --validate-only

# 기존 .env/config를 사용해 실제 평가. output은 존재하지 않는 새 디렉터리여야 한다.
python scripts/evaluate_ai.py --live --output /tmp/cra-evaluation-run-001

# 추가 검증 9건으로 실행 (최대 11회 요청)
python scripts/evaluate_ai.py --live --dataset evaluation/reviews.validation.v1.json --output /tmp/cra-validation-run-001
```

기본 제공자는 현재 설정을 사용하며 라우팅 서버의 `gpt-5-mini`를 그대로 테스트할 수 있다.
향후 llama-server에서도 같은 세트·버전·조건을 사용해 비교한다. 모델 이름 변경만으로
해당 서버의 모델 지원 여부가 보장되지는 않는다.

- N건의 단건 분석과 B개 근거 배치·최종 요약 1회를 순차 실행한다. 기본 18건 세트는 한 배치일 때 최대 20회다.
- 재시도는 0으로 고정한다. 호출 시간이 설정의 timeout을 넘으면 실패로 기록한다.
- 출력 폴더에 평가용 `reviews.sqlite`를 새로 생성한다. 앱의 DB 경로 설정은 사용하지 않는다.
- 이미 존재하는 출력 폴더를 거부하며 이전 결과를 재사용하지 않는다.
- 성공한 분석만 다시 실행해 모두 skip되고 추가 API 호출이 없음을 검사한다.
- 인사이트 집계와 통계가 동일한 성공 리뷰 집합을 나타내는지, 리포트 생성 후 분석이
  보존되는지 확인한다. 인사이트가 실패해도 통계 리포트는 생성하고 구조 검증은 실패한다.

## 결과 해석

`evaluation.json`은 단건 완료마다 체크포인트를 기록하며 실행 종료 시 지표를 추가한다.
`reviews.sqlite`, `report.txt`, `report.md`를 함께 남긴다. 분석 결과·키워드·합성 원문·기대 라벨과
각 요청의 응답 모델명·소요 시간을 기록한다. API 키·서버 오류 본문은 저장하지 않는다.
근거 단계의 검증된 인용·label을 `calls[].evidence`에 기록한다. 합성 평가에서 거부된 모델의
근거 응답은 `rejected_evidence_response`에 보존해 JSON·인용 오류를 진단하며, 키는 마스킹한다.
이는 HTTP/SDK 예외 본문과 구분한다. 근거 단계 자체가 실패하면 최종 요약을 호출하지 않는다.

| 지표 | 의미 |
| --- | --- |
| `accuracy_all` | 기대 감정과 일치한 수 / 전체 사례 수. API 실패·미완료도 분모에 포함 |
| `accuracy_valid` | 일치한 수 / 유효 분석 수. 유효 분석이 없으면 null |
| `confusion_matrix` | 행은 기대 감정, 열은 예측 감정·error·not_run |
| `per_class` | 감정별 support·precision·recall·F1. 분모가 0이면 해당 점수는 0 |
| `macro_f1` | 세 감정 F1의 단순 평균. 특정 감정이 없는 별도 세트에서도 세 감정을 모두 평균 |
| `metrics_by_language` | 평가셋의 `language` 표식별 동일 분류 지표. 없는 표식은 `unspecified` |
| `latency` | 실패를 포함한 단건 요청 평균·p95(오름차순 ceil(0.95×N)번째), 초 단위 |
| `structural_checks_passed` | 분석 저장·재실행 skip·인사이트·키워드 정합성·리포트·상태 보존 검사 결과 |
| `narrative_review` | 사람이 근거를 검토하기 전에는 pending |

각 호출의 `response_received`는 어댑터가 응답을 반환했다는 뜻이다. 이후 형식 검증에서
실패할 수 있으므로 분석 성공 여부는 `rows.status`를 기준으로 확인한다.
신뢰도 값은 모델의 자기평가이며 분류 정확도나 검증된 확률을 의미하지 않는다.
토큰 사용량과 비용은 현재 ProviderResponse에 없으므로 추정 금액을 출력하지 않는다.

종료 코드 0은 구조 검증 성공이다. 감정 오분류가 없어야 하거나 특정 F1 이상이어야 하는
품질 기준은 아직 팀에서 합의하지 않았으므로 자동 품질 통과로 해석하지 않는다.
종료 코드 1은 실행/구조 실패다. 라벨 불일치와 인사이트 품질은 결과를 읽고 판단한다.

## 근거 검토 및 반복 평가

인사이트는 [검토 기준](../evaluation/RUBRIC.md)에 따라 주장별로 근거 리뷰 ID를 기록한다.
사람 검토자는 기대 라벨의 적절성과 함께 확인한다. AI의 자체 검토는 별도 보조 기록이다.
반복 실행은 매번 새 출력 폴더를 사용해 변동을 보존하고 모델·데이터 해시·프롬프트 버전·
설정과 함께 비교한다. 이 도구는 모델을 자동 변경하거나 프롬프트를 최적화하지 않는다.

## 자동 테스트

한국어·영어·한영 혼합 각 9건의 `evaluation/reviews.multilingual.v1.json`도 제공한다.
사례의 선택 필드 `language`는 `ko|en|mixed`이며 평가 결과 분리에만 사용한다.
기존 평가 파일에 이 필드가 없어도 지원한다. 실행과 지표 해석은
[다국어 감정 분석 안내](MULTILINGUAL_SENTIMENT.md)를 참고한다.

```bash
python -m unittest discover -s tests -p 'test_evaluation.py' -v
python -m unittest discover -s tests -v
```

자동 테스트는 가짜 제공자를 주입하며 실 API를 호출하지 않는다. 점수 계산, 라벨 비전송,
입력 검증, 호출 상한, 실패와 부분 성공, 체크포인트, 키 비노출, 서비스 흐름을 검증한다.
이 브랜치는 PR #13 위에서 분기했으며 통합 평가에 #12·#13 구현을 사용한다.

첫 실제 실행의 수치, 근거 검토와 개선 후보는 [v1 실행 기록](../evaluation/BASELINE_V1.md)에 있다.

## 인사이트 프롬프트 비교

추가 검증의 원문과 기준은 [검증 사례 v1](../evaluation/VALIDATION_V1.md)에 고정했다.
`scripts/compare_insights.py`는 성공한 기존 평가의 원문·감정·키워드를 재사용해 보존된
`review-insights-v2` 단일 요청과 현재 v5의 근거 배치·요약을 비교한다. DB나 감정 분석은 다시 실행하지
않는다. 원본 데이터 해시, 사례 내용과 성공 상태가 일치해야 한다.

```bash
python scripts/compare_insights.py --live \
  --dataset evaluation/reviews.validation.v1.json \
  --saved-evaluation /tmp/cra-validation-run-001/evaluation.json \
  --timeout-seconds 90 \
  --output /tmp/cra-validation-comparison-001
```

비교도 새 출력 폴더만 허용하며 재시도 없이 기존 v2 1회 + B개 근거 배치 + 요약 1회를 요청한다(기본 최대 102회). `comparison.json`에는
시스템 프롬프트 원문, 같은 원본 입력인지 확인할 해시, 사용한 평가 결과 파일의 해시,
모델·설정·시각·응답·오류 유형이 남는다. 저장된 평가의 분석 모델은 원본 파일에서 확인하고,
이번 인사이트 요청 모델과 구분한다. 비교에 실패한 응답도 보존하며 기존 결과는 변경하지 않는다.
`completed`는 실행·형식·근거·포괄성 검사와 원본 입력 일치 결과이며 의미적 품질 개선의 판정이 아니다.
합성 입력 이외의 실제 고객 리뷰를 넣는 경우에는 결과 파일에 원문과 생성 내용이 남는 점을
고려해야 한다. 리포트 제목은 두 포맷 모두 리뷰 전체 감정에 따른 키워드 목록임을 명시한다.

실제 비교 결과와 남은 누락·가정 문제는 [v2 비교 기록](../evaluation/INSIGHT_V2_COMPARISON.md)에 있다.

현재 흐름만 재실행하려면 `--current-only`를 추가한다(근거 배치 B회 + 요약 1회, 기본 최대 101회). 이때 `same_input=null`이며
짝 비교를 수행했다고 표시하지 않는다. `--case-id bp01`처럼 지정하면 저장된 사례 중 해당
ID만 선택하며, 여러 번 지정할 수 있다. 실제 선택은 원본 순서를 유지하고 결과의 `case_ids`에
남긴다. 알 수 없는 ID나 중복 ID는 호출 전에 거부한다.

한 배치의 전체 근거 경로에서는 기존 흐름과 같은 원문·감정·키워드가 들어간다. 최종 요청에는 검증한
근거와 코드가 만든 개선 제안 후보가 추가된다. `same_input=true`는 원본 입력의 일치를
의미하며 모든 요청·스키마가 동일하다는 뜻이 아니다. `--timeout-seconds`는 해당 평가에만
적용하는 명시적 요청 제한이다. 실패를 지우거나 같은 출력 폴더를 덮어쓰지 않는다.

추가 [6건의 실행 전 기준](../evaluation/VALIDATION_V2.md), 실패를 포함한 전체 시도와 최종
품질 검토는 [v4 검증 결과](../evaluation/INSIGHT_V4_COMPARISON.md)에 기록했다.

## v5 분할 평가 및 품질 판정

현재 추출은 최대 20건/24,000자씩 분할하고 기본 100배치까지 허용한다.
전체 평가 요청 상한은 N(분류) + 101(추출)이며 재시도는 0이다. 기존 18건처럼 한 배치에
들어가는 입력은 여전히 N+2회다. `compare_insights.py --batch-size 8`로 같은 저장 분석을
작게 나눠 검증할 수 있다. 비교의 `input_sha256`/`same_input`은 **분할 전 전체 선택 입력**을
대조하며, 실제 요청별 해시는 `calls[].request_sha256`에 따로 남긴다.
`same_input=true`가 요청 구조까지 같다는 뜻은 아니다.

반환된 `insight.evidence_groups`에서 DB 내부 ID와 인용을 확인할 수 있다. 이 평가 도구의
내부 ID는 고정 cases 순서의 1부터 시작하는 번호이며, `rows[id - 1].id`가 검토용 case ID다.
기대 라벨과 검토 결과는 모델 입력에 포함하지 않는다.

저장된 평가를 네트워크 호출 없이 판정하는 방법은 [품질 판정 안내](AI_QUALITY_GATE.md)를
참고한다. 정책 파일을 명시해야 실행되며, 예시 정책은 팀 승인된 품질 기준이 아니다.

요청별 시스템 프롬프트 해시는 `calls[].system_prompt_sha256`으로 구분한다.
실제 v5 실행·실패·리포트 검증 기록은 [BATCHING_V5.md](../evaluation/BATCHING_V5.md)에 있다.
