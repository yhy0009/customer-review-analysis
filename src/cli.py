"""Command-line interface for the customer review analysis application.

This module owns only the CLI contract. Business logic is supplied through
command handlers so each feature module can be developed independently.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence


CommandHandler = Callable[[argparse.Namespace], Optional[int]]


def _positive_int(value: str) -> int:
    """Return a positive integer or raise an argparse-friendly error."""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("양의 정수를 입력해야 합니다.") from exc

    if number < 1:
        raise argparse.ArgumentTypeError("1 이상의 정수를 입력해야 합니다.")
    return number


def _rating(value: str) -> int:
    """Validate that a rating is in the supported 1-5 range."""
    rating = _positive_int(value)
    if rating > 5:
        raise argparse.ArgumentTypeError("별점은 1에서 5 사이여야 합니다.")
    return rating


def _iso_date(value: str) -> str:
    """Validate an ISO 8601 date while preserving the CLI string value."""
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("날짜는 YYYY-MM-DD 형식이어야 합니다.") from exc
    return value


def _add_period_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--date-from",
        type=_iso_date,
        help="조회 시작일(YYYY-MM-DD)",
    )
    parser.add_argument(
        "--date-to",
        type=_iso_date,
        help="조회 종료일(YYYY-MM-DD)",
    )


def _add_sentiment_filter(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--sentiment",
        choices=("positive", "neutral", "negative"),
        help="감정 분석 결과 필터",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build and return the application's argument parser."""
    parser = argparse.ArgumentParser(
        prog="customer-review-analysis",
        description="AI 기반 고객 리뷰 감정 분석 CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.json"),
        help="설정 파일 경로",
    )
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default="INFO",
        help="콘솔 로그 레벨",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="서브커맨드",
        metavar="COMMAND",
        required=True,
    )

    import_parser = subparsers.add_parser(
        "import",
        help="CSV/Excel 리뷰를 raw 저장소에 적재",
        description="CSV 또는 Excel 파일의 리뷰를 raw 저장소에 적재합니다.",
    )
    import_parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="가져올 CSV 또는 Excel 파일",
    )
    import_parser.add_argument(
        "--policy",
        choices=("skip", "upsert"),
        help="중복 리뷰 처리 정책(미지정 시 설정 파일 사용)",
    )

    clean_parser = subparsers.add_parser(
        "clean",
        help="raw 리뷰를 검증·정규화하여 clean 저장소에 적재",
        description="raw 리뷰에 정제 규칙과 중복 정책을 적용합니다.",
    )
    clean_parser.add_argument(
        "--policy",
        choices=("skip", "upsert"),
        help="중복 리뷰 처리 정책(미지정 시 설정 파일 사용)",
    )
    clean_parser.add_argument(
        "--min-length",
        type=_positive_int,
        help="허용할 리뷰의 최소 글자 수(미지정 시 설정 파일 사용)",
    )

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="AI API로 리뷰 감정과 신뢰도 분석",
        description="분석 대상을 선택하여 감정과 신뢰도 점수를 저장합니다.",
    )
    analyze_target = analyze_parser.add_mutually_exclusive_group(required=True)
    analyze_target.add_argument(
        "--all",
        dest="analyze_all",
        action="store_true",
        help="모든 clean 리뷰를 분석 대상으로 선택",
    )
    analyze_target.add_argument(
        "--id",
        dest="review_id",
        type=_positive_int,
        help="특정 리뷰 ID만 분석",
    )
    analyze_target.add_argument(
        "--unanalyzed",
        action="store_true",
        help="아직 분석되지 않은 리뷰만 선택",
    )
    analyze_parser.add_argument(
        "--limit",
        type=_positive_int,
        help="한 번에 분석할 최대 리뷰 수",
    )
    analyze_parser.add_argument(
        "--force",
        action="store_true",
        help="기존 분석 결과가 있어도 다시 분석",
    )

    extract_parser = subparsers.add_parser(
        "extract",
        help="AI 기반 키워드·요약·개선 제안 추출",
        description="조건에 맞는 리뷰를 종합하여 AI 인사이트를 추출합니다.",
    )
    _add_sentiment_filter(extract_parser)
    _add_period_filters(extract_parser)
    extract_parser.add_argument("--product", help="제품명 필터")
    extract_parser.add_argument(
        "--limit",
        type=_positive_int,
        help="AI 요청에 포함할 최대 리뷰 수",
    )

    list_parser = subparsers.add_parser(
        "list",
        help="리뷰 목록 조회",
        description="필터, 정렬, 페이지네이션을 적용해 리뷰 목록을 조회합니다.",
    )
    _add_sentiment_filter(list_parser)
    _add_period_filters(list_parser)
    list_parser.add_argument("--rating", type=_rating, help="별점 필터(1-5)")
    list_parser.add_argument("--product", help="제품명 필터")
    list_parser.add_argument("--page", type=_positive_int, default=1, help="페이지 번호")
    list_parser.add_argument(
        "--size",
        type=_positive_int,
        default=20,
        help="페이지당 리뷰 수",
    )
    list_parser.add_argument(
        "--sort",
        choices=("id", "date", "rating", "sentiment"),
        default="id",
        help="정렬 기준",
    )
    list_parser.add_argument(
        "--order",
        choices=("asc", "desc"),
        default="desc",
        help="정렬 방향",
    )

    show_parser = subparsers.add_parser(
        "show",
        help="특정 리뷰 상세 조회",
        description="리뷰 원문과 감정 분석 결과를 함께 조회합니다.",
    )
    show_parser.add_argument(
        "--id",
        dest="review_id",
        type=_positive_int,
        required=True,
        help="조회할 리뷰 ID",
    )

    stats_parser = subparsers.add_parser(
        "stats",
        help="전체 또는 조건별 리뷰 통계 출력",
        description="리뷰 수, 감정별 비율, 평균 별점 등의 통계를 출력합니다.",
    )
    _add_sentiment_filter(stats_parser)
    _add_period_filters(stats_parser)
    stats_parser.add_argument("--product", help="제품명 필터")

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="정적 대시보드 차트와 종합 리포트 생성",
        description="감정 분포, 시간별 추이, 별점별 감정 분포 차트를 생성합니다.",
    )
    _add_period_filters(dashboard_parser)
    dashboard_parser.add_argument("--product", help="제품명 필터")
    dashboard_parser.add_argument(
        "--output",
        type=Path,
        default=Path("output"),
        help="차트와 리포트를 저장할 디렉터리",
    )
    dashboard_parser.add_argument(
        "--report-format",
        choices=("txt", "md"),
        default="md",
        help="종합 리포트 파일 형식",
    )

    export_parser = subparsers.add_parser(
        "export",
        help="분석 결과를 파일로 내보내기",
        description="필터링된 리뷰와 분석 결과를 지정 형식으로 내보냅니다.",
    )
    export_parser.add_argument(
        "--format",
        choices=("csv", "jsonl", "excel"),
        required=True,
        help="내보낼 파일 형식",
    )
    export_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="내보낼 파일 경로",
    )
    _add_sentiment_filter(export_parser)
    _add_period_filters(export_parser)
    export_parser.add_argument(
        "--rating-min",
        type=_rating,
        help="내보낼 리뷰의 최소 별점(1-5)",
    )
    export_parser.add_argument("--product", help="제품명 필터")

    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments for application code and tests."""
    return build_parser().parse_args(argv)


def dispatch(
    args: argparse.Namespace,
    handlers: Optional[Mapping[str, CommandHandler]] = None,
) -> int:
    """Dispatch a parsed command to an injected business-logic handler."""
    handler = (handlers or {}).get(args.command)
    if handler is None:
        print(
            f"[ERROR] '{args.command}' 명령의 기능 모듈이 아직 연결되지 않았습니다.",
            file=sys.stderr,
        )
        return 2

    result = handler(args)
    return 0 if result is None else result


def main(
    argv: Optional[Sequence[str]] = None,
    handlers: Optional[Mapping[str, CommandHandler]] = None,
) -> int:
    """Run the CLI and return a process exit code."""
    return dispatch(parse_args(argv), handlers=handlers)
