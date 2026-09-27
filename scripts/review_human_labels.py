"""Validate human labels, export domain datasets, or rescore saved results offline."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.errors import ValidationError
from src.evaluation import classification_metrics, _unique_object

FIELDS = ('id', 'category', 'language', 'rating', 'product_name', 'review_text')
LABELS = ('positive', 'neutral', 'negative')


def source_digest(case):
    raw = json.dumps({key: case[key] for key in FIELDS}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique_object)


def validate_labels(packet):
    if not isinstance(packet, dict) or not all(isinstance(packet.get(k), str) and packet[k].strip()
            for k in ('version', 'provenance', 'reviewer', 'reviewed_at')):
        raise ValidationError('사람 검토자와 검토 시각을 입력하세요. AI가 대신 확정한 라벨은 사용하지 마세요.')
    try:
        timestamp = datetime.fromisoformat(packet['reviewed_at'].replace('Z', '+00:00'))
        if timestamp.utcoffset() is None:
            raise ValueError
    except ValueError:
        raise ValidationError('검토 시각은 시간대를 포함한 ISO 8601 형식이어야 합니다.') from None
    cases = packet.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValidationError('검토할 리뷰가 없습니다.')
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or not set(FIELDS) <= set(case):
            raise ValidationError('검토 원문 필드가 누락됐습니다.')
        if any(not isinstance(case[k], str) or not case[k].strip()
               for k in ('id', 'product_name', 'review_text')):
            raise ValidationError('리뷰 ID·대상 이름·원문이 필요합니다.')
        if case['id'] in ids or case['category'] not in ('product', 'movie') or case['language'] not in ('ko', 'en', 'mixed'):
            raise ValidationError('중복 ID 또는 올바르지 않은 유형·언어입니다.')
        if type(case['rating']) is not int or not 1 <= case['rating'] <= 5:
            raise ValidationError('검토 자료의 별점은 1~5 정수여야 합니다.')
        ids.add(case['id'])
        if case.get('source_sha256') != source_digest(case):
            raise ValidationError(f"{case['id']}: 검토 대상 원문이 변경됐습니다.")
        if case.get('human_label') not in (*LABELS, 'exclude') or not isinstance(case.get('reason'), str) or not case['reason'].strip():
            raise ValidationError(f"{case['id']}: 사람 라벨과 판단 이유를 입력하세요. 판단 보류는 exclude와 이유로 기록하세요.")
    return cases


def export_dataset(packet, domain):
    cases = validate_labels(packet)
    kept = [c for c in cases if c['category'] == domain and c['human_label'] != 'exclude']
    if not kept:
        raise ValidationError('선택한 유형에 확정된 리뷰가 없습니다.')
    return {'version': f"{packet['version']}-{domain}-human",
            'provenance': f"Human labels declared by {packet['reviewer']} at {packet['reviewed_at']}. "
                'Small development fixtures; not an independent population benchmark. '
                f"Excluded IDs: {[c['id'] for c in cases if c['category'] == domain and c['human_label'] == 'exclude']}",
            'cases': [dict({k: c[k] for k in FIELDS}, expected=c['human_label'], reason=c['reason']) for c in kept]}


def rescore(packet, evaluation, domain):
    cases = [c for c in validate_labels(packet) if c['category'] == domain]
    rows = evaluation.get('rows')
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValidationError('저장된 평가 rows 형식을 확인하세요.')
    by_id = {r.get('id'): r for r in rows}
    required = {c['id'] for c in cases if c['human_label'] != 'exclude'}
    allowed = {c['id'] for c in cases}
    if len(by_id) != len(rows) or not required <= set(by_id) <= allowed:
        raise ValidationError('선택한 유형의 확정 리뷰가 모두 있어야 하며, 중복·다른 리뷰는 허용하지 않습니다.')
    scored = []
    for case in cases:
        row = by_id.get(case['id'])
        if row is None:
            continue
        if any(row.get(key) != case[key] for key in FIELDS):
            raise ValidationError(f"{case['id']}: 평가 결과의 입력과 검토 원문이 다릅니다.")
        status, prediction = row.get('status'), row.get('predicted')
        if status not in ('ok', 'error', 'not_run') or (status == 'ok' and prediction not in LABELS) or (status != 'ok' and prediction is not None):
            raise ValidationError('평가 상태 또는 예측값이 올바르지 않습니다.')
        if case['human_label'] != 'exclude':
            scored.append(dict(row, expected=case['human_label']))
    if not scored:
        raise ValidationError('점수를 계산할 확정 리뷰가 없습니다.')
    return {'domain': domain, 'reviewer': packet['reviewer'], 'reviewed_at': packet['reviewed_at'],
            'label_source': 'human_declared', 'api_calls': 0,
            'excluded_ids': [c['id'] for c in cases if c['human_label'] == 'exclude'],
            'metrics': classification_metrics(scored)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--domain', choices=('product', 'movie'), required=True)
    parser.add_argument('--evaluation', type=Path, help='저장된 결과를 재채점합니다. 생략하면 평가 입력 JSON을 내보냅니다.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        packet = read_json(args.labels)
        result = rescore(packet, read_json(args.evaluation), args.domain) if args.evaluation else export_dataset(packet, args.domain)
        # Never overwrite labels, source results or a previously exported artifact.
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print(f'완료: {args.output} (AI 호출 없음)')
        return 0
    except (OSError, ValueError, TypeError, KeyError, ValidationError) as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
