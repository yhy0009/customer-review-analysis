# 다국어 감정 분석

기존 `analyze` 명령에서 **한국어·영어·한영 혼합 리뷰**를 원문으로 분석한다.
언어 선택 옵션이나 사전 번역 단계 없이, 설정한 AI 모델이 원문 언어와 문맥을 해석한다.
분석 API 연결과 키 설정은 [AI CLI 안내](AI_CLI.md)를 따른다.

## 사용

```bash
# 한국어·영어·혼합 리뷰 각 3건, 총 9건의 합성 예제
python main.py import --file data/sample_multilingual_reviews.csv
python main.py clean

# 실제 AI API 호출: 미분석 리뷰를 대상으로 실행
python main.py analyze --unanalyzed --limit 20
python main.py stats
python main.py export --format jsonl --output output/multilingual.jsonl
```

명령은 설정된 DB를 사용하므로 기존 미분석 리뷰도 선택될 수 있다. 예제만 독립적으로 실행하려면
새 SQLite 경로를 지정한 별도 설정 파일을 만들어 모든 명령에 `--config`를 지정한다.
샘플은 실행 예시용 합성 데이터이며 실제 고객 리뷰나 실제 분석 결과가 아니다.

CSV·Excel의 `review_text`에 영어·한국어를 그대로 넣으면 된다. 문장부호·영어 축약형·이모지는
정제 과정에서 삭제하지 않으며 기존 공백 정규화·필수값·최소 길이 검증을 동일하게 적용한다.
기본 최소 길이는 3자이므로 `OK` 같은 2자 리뷰도 포함하려면 `clean --min-length 2`를 사용한다.

## 분석과 출력 기준

- 모든 언어에서 `positive`, `neutral`, `negative` 세 감정 값을 사용한다.
- 본문의 실제 경험과 전체 평가를 우선하며 별점은 보조 정보다.
- 부정어의 범위·축약형·이중 부정·반어·대조 표현·혼합 감정을 문맥으로 해석하도록 지시한다.
- 영어이거나 언어가 섞였다는 이유만으로 중립 또는 낮은 신뢰도로 처리하지 않는다.
- 요약과 키워드는 한국어로 생성하도록 요청한다. 같은 뜻의 한국어·영어 주제는 한국어 표현으로
  통일하며 필요한 제품명·브랜드 표기는 보존한다. 응답 후 별도 번역 API는 호출하지 않는다.
- 감정 값·신뢰도 범위·JSON 스키마·키워드 개수는 기존 로컬 검증을 적용한다. 요약의 한국어 사용,
  의미 정확성·번역 품질과 동의어 통일은 모델에 대한 지시이며 코드가 의미적으로 보장하지 않는다.
- 리뷰나 제품명에 들어 있는 한국어·영어 명령은 데이터로 취급하도록 시스템 지시와 분리한다.
- 모델 입력은 기존과 같이 제품명·별점·원문 세 필드다. 원본 리뷰 ID·언어 정답·평가 라벨은 전송하지 않는다.

지원 범위와 평가 데이터는 한국어·영어·한영 혼합이다. 다른 언어에 대해서는 지원 품질을
검증하지 않았다. 실제 성능은 선택한 모델과 데이터에 따라 달라지며 confidence는 모델의
자기평가로서 검증된 확률이 아니다.

## 기존 분석 결과와 프롬프트 버전

새 결과에는 `prompt_version=review-sentiment-v2-multilingual`을 기록한다.
DB 스키마·응답 필드는 유지하며 기존 `review-sentiment-v1` 결과도 그대로 조회·내보낼 수 있다.
기존 분석은 기본적으로 건너뛰므로 버전 변경만으로 자동 재분석하거나 비용을 발생시키지 않는다.

```bash
# list/show로 ID를 확인한 뒤 선택한 리뷰를 새 프롬프트로 다시 분석
python main.py analyze --id 123 --force
```

단건 Python 호출에서는 `AnalysisOptions.prompt_version=None` 또는 새 버전을 사용한다.
옛 버전을 명시적으로 요청하면 지원하지 않는 버전 오류를 반환한다. 이 검증은 API 호출 전에
수행한다. 새 버전으로 기존 결과를 다시 분석하려면 명시적인 `--force`가 필요하다.
재분석 실패 시 기존 결과가 제거되고 `ANALYSIS_FAILED`가 되는 기존 저장소 규칙도 유지한다.

## 언어별 평가

`evaluation/reviews.multilingual.v1.json`에는 한국어·영어·혼합 각 9건, 총 27건이 있다.
각 언어에서 긍정·중립·부정은 각 3건이며 부정 표현·반어·대조·평가 유보·별점 불일치·
리뷰 안의 출력 지시 등을 포함한다. 사례별 `language`는 평가용 수동 표식이며 모델 입력에
포함하지 않는다. 자동 언어 감지의 정확도를 측정하는 필드가 아니다.

데이터와 기대 라벨은 AI가 작성한 **개발용 초안**이며 사람 검토와 독립적인 보류 세트 평가가
필요하다. 개발 프롬프트와 유사한 사례를 포함하므로 독립 성능 시험의 결과로 취급하지 않는다.

```bash
# 설정·SDK·API 키·DB 없이 데이터 형식과 언어별 건수 확인
python scripts/evaluate_ai.py --validate-only --dataset evaluation/reviews.multilingual.v1.json

# 실제 모델 평가: 존재하지 않는 새 출력 디렉터리 사용
python scripts/evaluate_ai.py --live --dataset evaluation/reviews.multilingual.v1.json \
  --output output/multilingual-evaluation-001
```

실제 평가는 기존 전체 평가 흐름을 사용한다. 정상 처리·기본 배치 설정에서는 감정 분석 27회,
근거 추출 2회, 인사이트 요약 1회로 총 30회 API를 호출하며 재시도는 0이다.
별도의 평가 DB를 생성하고 앱의 DB를 사용하지 않는다. 인사이트 또는 보고서의 구조 검증은
감정 분류의 의미적 정확도와 별개다.

`evaluation.json`과 콘솔 JSON의 `metrics_by_language`에서 언어별 다음 지표를 확인한다.

- 전체·유효 분석 건수, API 실패·미완료 건수
- 전체 기준·유효 분석 기준 정확도
- 감정별 precision·recall·F1, macro F1, 혼동 행렬

실패·미완료도 해당 언어의 전체 정확도 분모에 포함한다. 기존 언어 표식이 없는 평가셋은
`unspecified`로 묶어 이전 파일 형식을 계속 지원한다. SHA-256·프롬프트 버전·모델 정보는
기존과 같이 저장한다. 품질 판정 도구의 정책은 기존 전체 지표 기준이며 언어별 통과 기준은
별도 자동 판정하지 않는다.

## 자동 검증 범위

```bash
python -m unittest tests.test_multilingual_sentiment tests.test_multilingual_evaluation tests.test_ai_cli -q
python -m unittest discover -s tests -q
```

자동 테스트는 실제 CSV·Excel, 정제·SQLite·CLI·내보내기를 사용하고 AI 제공자만 대체한다.
원문 보존·한국어 응답 저장·명령 분리·버전 기록·기존 결과 건너뛰기·강제 재분석·언어별 지표와
실패 처리·SDK 없는 데이터 검증을 확인한다. **모의 응답의 점수는 실제 모델의 분류 정확도가 아니다.**
이번 개발에서는 실제 API 기반 언어별 정확도 평가를 실행하지 않았다.
