"""Opt-in live connectivity check using three synthetic Korean reviews.

Run from the project root: python scripts/smoke_ai.py --live
No SQLite writes. No retries. Results never include credentials or raw API errors.
"""

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analyzer import analyze_review
from src.config import load_config, load_env_file
from src.errors import AppError
from src.models import AnalysisOptions, CleanReview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow up to 3 billable API requests")
    parser.add_argument("--output", type=Path, help="Optional JSON result path (no credentials)")
    args = parser.parse_args()
    if not args.live:
        parser.error("실제 API 호출에는 --live가 필요합니다.")
    load_env_file()
    ai = load_config("config/config.json")["ai"]
    options = AnalysisOptions(
        provider=ai["provider"], model=ai["model"], api_key=ai["api_key"],
        base_url=ai.get("base_url"), timeout_seconds=ai["timeout_seconds"], max_retries=0,
        reasoning_effort=ai.get("reasoning_effort"),
    )
    samples = [
        ("positive", 5, "배송도 빠르고 음질이 정말 좋아요. 가격 대비 만족해서 추천합니다."),
        ("neutral", 3, "검은색 제품을 주문했고 상자 안에는 본체와 설명서가 들어 있습니다."),
        ("negative", 1, "사용 하루 만에 고장 났고 고객센터도 응답이 없습니다. 매우 실망했습니다."),
    ]
    report = {"provider": options.provider, "requested_model": options.model,
              "checked_at": datetime.now(timezone.utc).isoformat(), "results": []}
    for index, (expected, rating, content) in enumerate(samples, 1):
        review = CleanReview(
            id=index, source_review_id=None, product_name="테스트 이어폰", rating=rating,
            review_date=date(2026, 9, 1), review_text=content,
            cleaned_at=datetime.now(timezone.utc),
        )
        started = time.monotonic()
        try:
            result = analyze_review(review, options)
        except AppError as exc:
            report["error"] = {"sample": index, "type": type(exc).__name__, "message": str(exc)}
            break
        report["results"].append({
            "sample": index, "expected": expected, "sentiment": result.sentiment.value,
            "matched": result.sentiment.value == expected, "confidence": result.confidence,
            "summary": result.summary, "keywords": result.keywords, "model": result.model,
            "elapsed_seconds": round(time.monotonic() - started, 2),
        })
    report["passed"] = len(report["results"]) == 3 and all(r["matched"] for r in report["results"])
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
