"""Explicit live extraction into a source-bound dashboard artifact; DB is read-only."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config, load_env_file, resolve_project_path
from src.errors import AppError
from src.models import AnalysisOptions
from src.sqlite_repository import SQLiteReviewRepository
from src.web_dashboard import encode, filters_from_dict, make_insight_artifact, select_analyzed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--product-name")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--sentiment", choices=("positive", "neutral", "negative"))
    args = parser.parse_args()
    if not 1 <= args.limit <= 2000:
        parser.error("limit은 1~2000이어야 합니다.")
    try:
        output = resolve_project_path(args.output)
        if output.exists():
            parser.error("새 output 파일을 지정하세요. 기존 파일은 덮어쓰지 않습니다.")
        filters = filters_from_dict({k: getattr(args, k) for k in ("product_name", "date_from", "date_to", "sentiment")})
        with SQLiteReviewRepository(resolve_project_path(args.database), read_only=True) as repository:
            with repository.read_snapshot():
                details = select_analyzed(repository, filters, args.limit)
        load_env_file()
        from src.insight_extractor import AIInsightExtractor
        options = AnalysisOptions(**load_config("config/config.json")["ai"])
        insight = AIInsightExtractor(options).extract_insights(details, filters)
        artifact = make_insight_artifact(details, insight, args.limit)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as target:
            target.write(encode(artifact))
        print(f"인사이트 저장 완료: {insight.review_count}건")
        return 0
    except (AppError, OSError) as exc:
        print(f"인사이트 준비 실패: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
