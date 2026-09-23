"""Console and portable table output for the same comparison snapshot."""
from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path

from src.comparison import ComparisonGroup, ComparisonRequest, ComparisonResult
from src.errors import OutputError
from src.models import Sentiment


FIELDS = (
    "name", "uncategorized", "product_count", "total_reviews", "analyzed_reviews",
    "unanalyzed_reviews", "failed_reviews", "average_rating", "analysis_completion_ratio",
    "positive_count", "neutral_count", "negative_count", "positive_ratio",
    "neutral_ratio", "negative_ratio", "insufficient_sample",
)


def group_record(group: ComparisonGroup, min_reviews: int) -> dict:
    stats = group.statistics
    record = {
        "name": group.name, "uncategorized": group.name is None,
        "product_count": group.product_count, "total_reviews": stats.total_reviews,
        "analyzed_reviews": stats.analyzed_reviews, "unanalyzed_reviews": stats.unanalyzed_reviews,
        "failed_reviews": stats.failed_reviews, "average_rating": stats.average_rating,
        "analysis_completion_ratio": stats.analyzed_reviews / stats.total_reviews if stats.total_reviews else None,
        "insufficient_sample": stats.analyzed_reviews < min_reviews,
    }
    for sentiment in Sentiment:
        count = stats.sentiment_counts.get(sentiment, 0)
        record[sentiment.value + "_count"] = count
        record[sentiment.value + "_ratio"] = count / stats.analyzed_reviews if stats.analyzed_reviews else None
    return record


def display_name(text: str) -> str:
    # Source names must not inject terminal controls into the comparison table.
    return "".join(char if char.isprintable() else " " for char in text)


def _percent(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "N/A"


def format_comparison(result: ComparisonResult, request: ComparisonRequest) -> str:
    dimension = "제품" if request.group_by == "product" else "카테고리"
    total = sum(group.statistics.total_reviews for group in result.groups)
    lines = [f"{dimension}별 비교: {len(result.groups)}개 그룹 / 정제 리뷰 {total}건",
             f"기간: {request.filters.date_from or '전체'} ~ {request.filters.date_to or '전체'}",
             "감정 비율: 분석 완료 리뷰 기준 / 평균 별점: 정제 리뷰 기준"]
    if request.category is not None:
        lines.append(f"카테고리 조건: {display_name(request.category)}")
    if request.filters.rating_min is not None:
        lines.append(f"최소 별점: {request.filters.rating_min}")
    if not result.groups:
        lines.append("조건에 맞는 정제 리뷰가 없습니다. import·clean 및 조회 조건을 확인하세요.")
    else:
        lines.append("이름 | 제품 수 | 리뷰 수 | 분석 | 미분석 | 실패 | 평균 별점 | 완료율 | 긍정 | 중립 | 부정 | 표본")
        for group in result.groups:
            row = group_record(group, request.min_reviews)
            rating = f"{row['average_rating']:.2f}" if row['average_rating'] is not None else "N/A"
            sample = "분석 없음" if not row['analyzed_reviews'] else (
                "부족" if row['insufficient_sample'] else "충족"
            )
            lines.append(" | ".join([
                display_name(group.label), str(row['product_count']), str(row['total_reviews']),
                str(row['analyzed_reviews']), str(row['unanalyzed_reviews']), str(row['failed_reviews']),
                rating, _percent(row['analysis_completion_ratio']),
                *(_percent(row[sentiment.value + '_ratio']) for sentiment in Sentiment), sample,
            ]))
        if len(result.groups) < 2:
            lines.append("비교 대상이 1개입니다. 다른 제품·카테고리 또는 기간을 포함하세요.")
        lines.append(f"표본 안내: 분석 {request.min_reviews}건 미만은 참고용입니다. 통계적 유의성을 판정하지 않습니다.")
    if result.missing_names:
        lines.append("현재 조건에 없는 이름: " + ", ".join(display_name(name) for name in result.missing_names))
    return "\n".join(lines)


def _check_target(path: Path, force: bool, protected: tuple[Path, ...]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OutputError("출력 파일로 심볼릭 링크나 디렉터리를 지정할 수 없습니다.")
    if any(path == item or (path.exists() and item.exists() and path.samefile(item)) for item in protected):
        raise OutputError("사용 중인 SQLite 저장소를 출력 파일로 지정할 수 없습니다.")
    if path.exists() and not force:
        raise OutputError("출력 파일이 이미 있습니다. 덮어쓰려면 --force를 지정하세요.")


def write_comparison(
    result: ComparisonResult, request: ComparisonRequest, *,
    font_family: str = "", dpi: int = 150, protected_paths: tuple[Path, ...] = (),
) -> list[Path]:
    if request.output is None:
        return []
    published: list[Path] = []
    try:
        output = request.output.resolve()
        output.mkdir(parents=True, exist_ok=True)
        stem = f"comparison_{request.group_by}_{result.generated_at:%Y%m%d_%H%M%S}"
        records = [group_record(group, request.min_reviews) for group in result.groups]
        with tempfile.TemporaryDirectory(prefix=".comparison-", dir=output) as temporary:
            stage = Path(temporary)
            csv_path = stage / (stem + ".csv")
            with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=FIELDS)
                writer.writeheader()
                for record in records:
                    row = dict(record)
                    name = row["name"]
                    if isinstance(name, str) and name.lstrip().startswith(("=", "+", "-", "@")):
                        row["name"] = "'" + name
                    writer.writerow(row)
            json_path = stage / (stem + ".json")
            json_path.write_text(json.dumps({
                "group_by": request.group_by,
                "generated_at": result.generated_at.isoformat(),
                "filters": {
                    "date_from": str(request.filters.date_from) if request.filters.date_from else None,
                    "date_to": str(request.filters.date_to) if request.filters.date_to else None,
                    "rating_min": request.filters.rating_min, "category": request.category,
                    "names": list(request.names),
                },
                "min_reviews": request.min_reviews,
                "missing_names": list(result.missing_names), "groups": records,
            }, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
            files = [csv_path, json_path]
            if request.chart:
                from src.comparison_charts import render_comparison_charts
                files.extend(render_comparison_charts(
                    result, request, stage, stem, font_family=font_family, dpi=dpi,
                ))
            for file in files:
                _check_target(output / file.name, request.force, protected_paths)
            for file in files:
                target = output / file.name
                _check_target(target, request.force, protected_paths)
                if request.force:
                    os.replace(file, target)
                else:
                    os.link(file, target)
                published.append(target)
        return published
    except ImportError:
        raise
    except Exception as exc:
        message = str(exc) if isinstance(exc, OutputError) else "비교 결과 파일 생성·저장에 실패했습니다."
        if published:
            message += " 이미 저장된 파일: " + ", ".join(str(path) for path in published)
        raise OutputError(message) from exc
