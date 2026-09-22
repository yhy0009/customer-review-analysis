"""Optional comparison command, independent of dashboard and AI services."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from src.comparison import ComparisonRequest, ComparisonService
from src.comparison_output import format_comparison, write_comparison
from src.config import resolve_project_path
from src.errors import ConfigError, OutputError, StorageError, ValidationError
from src.models import ReviewFilter


def add_comparison_parser(subparsers) -> None:
    parser = subparsers.add_parser("compare", help="제품·카테고리별 리뷰 통계 비교")
    parser.add_argument("--group-by", choices=("product", "category"), default="product", help="비교 기준 (기본: product)")
    parser.add_argument("--name", action="append", default=[], help="비교할 제품/카테고리 이름, 정규화 후 정확히 일치. 여러 번 지정 가능")
    parser.add_argument("--category", help="특정 카테고리의 리뷰만 비교 (정확히 일치)")
    parser.add_argument("--date-from", type=date.fromisoformat, help="리뷰 작성 시작일 YYYY-MM-DD (포함)")
    parser.add_argument("--date-to", type=date.fromisoformat, help="리뷰 작성 종료일 YYYY-MM-DD (포함)")
    parser.add_argument("--rating-min", type=int, choices=range(1, 6), help="최소 별점")
    parser.add_argument("--sort", choices=("name", "reviews", "rating", "negative"), default="name", help="정렬 기준 (기본: name)")
    parser.add_argument("--order", choices=("asc", "desc"), default="asc", help="정렬 방향 (기본: asc)")
    parser.add_argument("--min-reviews", type=int, default=5, help="표본 부족 안내의 최소 분석 건수 (기본: 5)")
    parser.add_argument("--output", type=Path, help="CSV·JSON 저장 디렉터리, 상대 경로는 프로젝트 기준")
    parser.add_argument("--chart", action="store_true", help="비교 PNG 추가 생성 (--output 필요, 20그룹마다 한 장)")
    parser.add_argument("--force", action="store_true", help="동일 이름 산출물 덮어쓰기 (--output 필요)")


def handle_comparison(args: argparse.Namespace) -> int:
    try:
        request = ComparisonRequest(
            group_by=args.group_by, names=tuple(args.name), category=args.category,
            filters=ReviewFilter(date_from=args.date_from, date_to=args.date_to, rating_min=args.rating_min),
            sort=args.sort, order=args.order, min_reviews=args.min_reviews,
            output=resolve_project_path(args.output) if args.output is not None else None,
            chart=args.chart, force=args.force,
        )
        from src.sqlite_repository import SQLiteReviewRepository

        with SQLiteReviewRepository.from_config(args.app_config) as repository:
            result = ComparisonService(repository).compare(request)
            database = repository.database_path
            protected = tuple([database] + [database.with_name(database.name + suffix)
                                             for suffix in ("-wal", "-shm", "-journal")])
            visualization = args.app_config["visualization"]
            files = write_comparison(
                result, request, font_family=visualization["font_family"],
                dpi=visualization["dpi"], protected_paths=protected,
            )
        print(format_comparison(result, request))
        for path in files:
            print(f"파일: {path}")
        return 0
    except (ConfigError, ValidationError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except ImportError:
        print("[ERROR] 비교 차트 생성에 필요한 패키지가 없습니다. requirements.txt 의존성을 확인하세요.", file=sys.stderr)
        return 2
    except (StorageError, OutputError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 3
