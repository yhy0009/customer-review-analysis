# 12건 사람 검토

[검토 입력 JSON](human_review_12.v1.json)은 제품 6건과 가상 영화 한 편의 리뷰 6건으로 구성했습니다. 모델 예측이나 기대 라벨은 넣지 않았습니다. 제품 원문은 기존 합성 개발 자료에서 골랐고 영화 원문은 새로 작성한 합성 문장입니다. 영화의 별점 3은 입력용 임시값입니다. 실제 고객 별점이나 중립 정답이 아닙니다. 이 자료는 결함 확인용이며 독립적인 실제 사용자 성능 표본이 아닙니다.

1. 파일을 복사한 뒤 `reviewer`와 시간대를 포함한 `reviewed_at`을 입력합니다. 검토자의 실명 대신 팀 내 식별자를 사용할 수 있습니다.
2. 각 원문을 읽고 `human_label`에 `positive`, `neutral`, `negative`를 적습니다. `reason`에는 원문에 근거한 판단 이유를 적습니다. 분명한 긍정·부정 평가가 없거나 비슷하게 섞인 경우 중립을 고려합니다. 별점과 충돌하면 본문의 평가를 우선합니다.
3. 판단하기 어려우면 `exclude`와 제외 이유를 적습니다. 보류 사례는 점수의 분모에서 빠지며 결과의 `excluded_ids`에 명시됩니다. 빈 라벨·이유는 완료로 처리하지 않습니다. AI로 빈칸을 채우고 사람 라벨이라고 기록하지 마세요.
4. 원문·ID·유형·별점·제목·언어는 수정하지 않습니다. 각 사례의 해시로 실수로 바뀐 입력을 확인합니다. 사람 확인을 기술적으로 인증하는 도구는 아니므로 검토자 기록과 실제 절차를 함께 관리합니다.

사람 검토를 마친 뒤 유형별 평가 파일을 만듭니다. 아래 명령은 AI를 호출하지 않습니다. 모든 사례에 확정 또는 제외 결정을 기록해야 합니다.

```bash
python scripts/review_human_labels.py --labels completed-labels.json --domain product --output product-human.json
python scripts/review_human_labels.py --labels completed-labels.json --domain movie --output movie-human.json
python scripts/evaluate_ai.py --validate-only --dataset product-human.json
python scripts/evaluate_ai.py --validate-only --dataset movie-human.json
```

`--live` 평가는 API를 호출합니다. 영화 인사이트를 검토하려면 영화 유형 지원 기능이 적용된 버전을 사용하세요. 도메인별로 실행하며 결과 디렉터리는 새 경로여야 합니다. 12건 전체를 한 파일로 합쳐 인사이트를 생성하지 않습니다.

이미 **같은 ID·원문·별점·대상·유형·언어**로 실행한 결과가 있으면 새 호출 없이 재채점할 수 있습니다. 해당 유형의 확정 리뷰가 모두 있어야 하며 입력이 다르거나 중복된 결과는 거부합니다. 기존 평가 파일은 수정하지 않습니다.

```bash
python scripts/review_human_labels.py --labels completed-labels.json --domain movie --evaluation saved-movie-evaluation.json --output movie-human-scores.json
```

이 재채점은 감정 분류만 다룹니다. 요약이 원문 뜻을 보존했는지, 근거와 제안이 타당한지는 원문과 출력 문장을 사람이 별도로 대조해야 합니다. 초기 검토지는 미완료이므로 내보내기가 실패하는 것이 정상입니다. 이전 65건 결과와 이번 12건의 점수를 합산하지 마세요.
