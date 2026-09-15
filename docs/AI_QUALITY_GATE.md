# 저장 평가의 품질 판정

`scripts/check_ai_quality.py`는 저장된 `evaluation.json`과 고정 데이터셋, 명시한 정책으로
판정한다. API·환경 파일·DB를 열지 않는다. 원래 평가와 검토 기록을 수정하지 않고 새 JSON을
생성한다. 기존 출력 파일은 덮어쓰지 않는다. 상대 경로는 이 개발 스크립트를 실행하는
작업 디렉터리 기준이며, 아래 명령은 프로젝트 루트에서 실행한다.

```bash
python scripts/check_ai_quality.py \
  --dataset evaluation/reviews.v1.json \
  --evaluation evaluation/results/2026-09-13/baseline-v1.json \
  --policy evaluation/quality-policy.example.json \
  --output /tmp/quality-first.json
```

예시 정책은 구현 사용법을 보여주는 **미승인 초안**이다. 팀의 합의나 사람 검토를 뜻하지 않는다.
기본 정책을 자동 적용하지 않으며 실행자가 `--policy`로 기준을 선택한다.
예시 정책에서는 사람 검토가 필요하므로 자동 지표가 좋아도 검토가 없으면 `pending`이다.

## 정책 계약

| 필드 | 의미 |
| --- | --- |
| version | 정책 식별자. 기준 변경 시 새 버전 권장 |
| dataset_sha256 | 고정 데이터셋 파일의 SHA-256 |
| minimums | accuracy_all, macro_f1, valid_ratio 각각의 최솟값(0~1) |
| max_analysis_p95_seconds | 실패 요청도 포함한 분류 요청 p95 상한(0~86400초) |
| max_regression | null 또는 위 세 지표별 baseline 대비 허용 하락 폭(0~1) |
| require_human_review | true이면 사람의 라벨 검토와 요약 검토 통과도 요구 |

전체 cases와 평가 rows의 ID·순서·본문·기대 라벨·판단 이유까지 대조한다. 평가 파일에 저장된
metrics를 신뢰하지 않고 rows에서 다시 계산한다. 오류와 미실행은 accuracy_all 및 recall의
분모에 남고, valid_ratio는 유효 분류/전체 사례다. 빈 데이터셋·비정상 수치·중복 JSON 키·
호출 수와 행 상태의 불일치는 입력 오류다. 이 도구는 현재 재시도 0회인 평가 형식을 받는다.

실행 완료와 저장·skip·근거 추출·키워드·리포트·원본 상태 보존 검사는 평가 파일의 기존
checks를 검사한다. 파일에 기록된 검사를 확인하는 것이며 DB/모델 실행을 다시 검증하는 것은 아니다.
파일 해시는 실행 간 연결을 확인하고, 제3자가 파일이나 검토자 이름을 위조하지 않았다는
인증 수단은 아니다.

`require_human_review=false`이면 자동 검사만 통과할 수 있다. 결과의 scope는
`automated_checks_only`이며 narrative_review는 계속 pending일 수 있다. 이를 요약 의미 품질
승인으로 표시하지 않는다. 구조 실패나 기준 미달이 있으면 사람 검토 대기보다 fail이 우선한다.

## 사람 검토 기록

[quality-review.example.json](../evaluation/quality-review.example.json)을 복사해 다음을 채운다.

1. 검토할 evaluation.json 파일의 SHA-256을 evaluation_sha256에 기록한다.
2. reviewer, reviewer_kind(human/assistant), 시간대가 있는 reviewed_at을 기록한다.
3. 고정 기대 라벨 검토까지 마쳤으면 labels_reviewed를 true로 기록한다.
4. rubric 여섯 항목에 verdict, 근거 case_ids, 판단 근거 note를 기록한다.

| 키 | 검토 기준 |
| --- | --- |
| grounding | 원문 근거 |
| numbers | 수치 정확성 |
| issues | 핵심 이슈 |
| suggestions | 개선 제안 |
| scope | 범위 구분 |
| injection | 데이터 안 지시 |

각 항목의 기준은 [RUBRIC.md](../evaluation/RUBRIC.md)를 따른다.
verdict는 pass/partial/fail/not_applicable이며, not_applicable은 수치가 없는 numbers만 허용한다.
나머지 항목에는 성공한 평가 행의 case ID가 하나 이상 필요하다. 모든 항목에는 note가 필요하다.
partial 또는 fail이 하나라도 있는 사람 검토는 fail이다. assistant 검토는 자동으로 사람의
승인이 되지 않고 pending에 머문다. 라벨 검토가 끝나지 않았을 때도 pending이다.
검토 대상 평가가 바뀌면 해시가 달라지므로 기존 기록을 재사용할 수 없다.

```bash
python scripts/check_ai_quality.py \
  --dataset evaluation/reviews.v1.json --evaluation /tmp/run/evaluation.json \
  --policy /tmp/team-policy-v1.json --review /tmp/review.json \
  --output /tmp/quality-reviewed.json
```

## 회귀 비교와 종료 코드

정책의 max_regression을 지정하면 `--baseline /path/to/evaluation.json`이 필수다.
baseline도 같은 데이터셋의 rows인지 확인하고 지표를 다시 계산한다. live/injected 모드가
다른 결과는 비교하지 않는다. 예를 들어 accuracy_all 허용 하락 .02는 2퍼센트포인트를 뜻한다.
모델·프롬프트가 바뀐 전후 비교는 가능하지만 실제 API 지연 시간과 출력은 변동될 수 있다.

- **0:** 지정 정책 통과. scope와 narrative_review를 함께 확인한다.
- **1:** 기준 미달 또는 사람 검토 대기. 결과 파일에 항목별 actual/required/status를 남긴다.
- **2:** 입력 형식·해시·출력 경로 오류. 모델이나 품질의 실패로 해석하지 않는다.

결과에는 정책·데이터셋·평가·baseline·검토 파일 해시를 기록한다. `comparison.json`은
감정 분류 평가 파일이 아니므로 이 판정 도구의 입력으로 받지 않는다.
