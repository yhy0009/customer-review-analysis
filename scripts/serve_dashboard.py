"""Serve the JS dashboard on 127.0.0.1; --demo uses isolated synthetic data."""

import argparse
import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import resolve_project_path
from src.dashboard_server import create_server
from src.errors import AppError
from src.models import AnalysisResult, CleanReview, DuplicatePolicy, RawReview, ReviewFilter, Sentiment
from src.sqlite_repository import SQLiteReviewRepository
from src.web_dashboard import DashboardData, encode, load_insight_artifact, select_analyzed, source_hash


def seed_demo(directory):
    """Replay archived synthetic analysis; no API or real application DB access."""
    database = directory / "demo.sqlite"
    baseline = json.loads((ROOT / "evaluation/results/2026-09-13/baseline-v1.json").read_bytes())
    insight = json.loads((ROOT / "evaluation/results/2026-09-16/v5-batch4-success.json").read_bytes())["runs"][0]["insight"]
    now = datetime.now(timezone.utc)
    with SQLiteReviewRepository(database) as repository:
        for i, row in enumerate(baseline["rows"], 1):
            repository.save_raw_reviews([RawReview(source_review_id=row["id"], product_name=row["product_name"],
                review_date="2026-09-01", rating=row["rating"], review_text=row["review_text"])], DuplicatePolicy.SKIP)
            repository.save_clean_reviews([CleanReview(id=i, product_name=row["product_name"],
                review_date=date(2026, 9, 1), rating=row["rating"], review_text=row["review_text"], cleaned_at=now)], DuplicatePolicy.SKIP)
            repository.save_analysis(AnalysisResult(review_id=i, sentiment=Sentiment(row["predicted"]),
                confidence=row["confidence"], summary=row.get("summary"), keywords=row["keywords"],
                analyzed_at=now, provider=baseline["provider"], model=row["model"]))
        details = select_analyzed(repository, ReviewFilter(), 50)
        # The archived artifact predates this envelope; bind it explicitly to this replay DB.
        artifact = {"schema_version": 1, "source_sha256": source_hash(details), "selection_limit": 50,
                    "review_ids": [d.review.id for d in details], "insight": insight}
    path = directory / "insight.json"
    path.write_bytes(encode(artifact))
    load_insight_artifact(path)
    return database, path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--insight-file", type=Path)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.demo and (args.database or args.insight_file):
        parser.error("--demo는 실제 DB·인사이트 경로와 함께 사용할 수 없습니다.")
    if not 0 <= args.port <= 65535:
        parser.error("port는 0~65535여야 합니다.")
    try:
        with tempfile.TemporaryDirectory(prefix="cra-dashboard-") as directory:
            database, insight = seed_demo(Path(directory)) if args.demo else (
                resolve_project_path(args.database or "data/app_database.db"),
                resolve_project_path(args.insight_file) if args.insight_file else None)
            data = DashboardData(database, insight_path=insight, demo=args.demo)
            with create_server(data, args.port) as server:
                print(f"Dashboard: http://127.0.0.1:{server.server_port} ({'합성 데이터 데모' if args.demo else 'SQLite 조회'})", flush=True)
                server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (AppError, OSError) as exc:
        print(f"대시보드 시작 실패: {type(exc).__name__}. DB·인사이트 경로와 포트를 확인하세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
