"""Opt-in insight connectivity check: one request with synthetic analyzed reviews."""

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config, load_env_file
from src.errors import AppError
from src.insight_extractor import AIInsightExtractor
from src.models import AnalysisOptions, AnalysisResult, CleanReview, ReviewDetail, ReviewFilter, Sentiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow one billable API request; no retries")
    args = parser.parse_args()
    if not args.live:
        parser.error("실제 API 호출에는 --live가 필요합니다.")
    load_env_file()
    ai = load_config("config/config.json")["ai"]
    options = AnalysisOptions(provider=ai["provider"], model=ai["model"], api_key=ai["api_key"],
        base_url=ai.get("base_url"), timeout_seconds=ai["timeout_seconds"], max_retries=0,
        reasoning_effort=ai.get("reasoning_effort"))
    now = datetime.now(timezone.utc)
    samples = [
        (Sentiment.POSITIVE, 5, "배송이 빠르고 음질이 좋아요.", ["배송", "음질"]),
        (Sentiment.NEGATIVE, 1, "배송이 사흘 늦고 포장도 찢어져 있었습니다.", ["배송", "포장"]),
        (Sentiment.NEGATIVE, 2, "예정일보다 배송이 이틀 늦어서 불편했습니다.", ["배송"]),
    ]
    reviews = [ReviewDetail(
        CleanReview(id=i, product_name="테스트 이어폰", rating=rating, review_date=date(2026, 9, 1),
                    review_text=content, cleaned_at=now),
        AnalysisResult(review_id=i, sentiment=sentiment, confidence=.9, keywords=keywords,
                       analyzed_at=now, provider="synthetic-fixture", model="manual"),
    ) for i, (sentiment, rating, content, keywords) in enumerate(samples, 1)]
    started = time.monotonic()
    try:
        result = AIInsightExtractor(options).extract_insights(reviews, ReviewFilter())
    except AppError as exc:
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}))
        return 1
    passed = (result.review_count == 3 and bool(result.summary)
              and [(k.keyword, k.count) for k in result.negative_keywords] == [("배송", 2), ("포장", 1)])
    print(json.dumps({"passed": passed, "provider": options.provider, "requested_model": options.model,
                      "elapsed_seconds": round(time.monotonic() - started, 2),
                      "result": asdict(result)}, ensure_ascii=False, indent=2, default=str))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
