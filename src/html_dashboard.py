"""Render a portable HTML dashboard with embedded PNGs and no remote assets."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from html import escape
from typing import Sequence

from src.errors import OutputError, ValidationError
from src.models import (
    KeywordCount, ReviewFilter, ReviewStatistics, Sentiment,
    SentimentChangeResult, SentimentChangeStatus,
)
from src.sentiment_alerts import format_sentiment_change


_SENTIMENTS = ((Sentiment.POSITIVE, "긍정"), (Sentiment.NEUTRAL, "중립"), (Sentiment.NEGATIVE, "부정"))
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_STYLE = """
:root{color-scheme:light;--ink:#172b36;--muted:#576b77;--line:#dce4e6;--blue:#0b6fd1}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:#f4f6f5;color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",sans-serif}
a{color:#075cab;text-underline-offset:4px}a:focus-visible{outline:3px solid #d69c22;outline-offset:4px}
.wrap{max-width:1200px;margin:auto;padding:42px 28px 30px}
.brand{font-size:12px;font-weight:800;letter-spacing:2px;color:#38626c;margin:0 0 12px}
.header-top,.section-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}
.badge{padding:5px 12px;border:1px solid #a8c7bd;border-radius:6px;color:#28634e;background:#edf6f1;font-size:12px;white-space:nowrap}
h1{font-size:34px;line-height:1.3;letter-spacing:-1px;margin:0 0 14px}h2{font-size:20px;margin:0 0 12px}h3{font-size:15px;margin:0 0 10px}
p{margin:8px 0}.muted,.stamp,.note{color:var(--muted)}.stamp,.note{font-size:13px}
.scope{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}.scope span{background:#fff;border:1px solid var(--line);padding:5px 12px;border-radius:6px;overflow-wrap:anywhere;max-width:100%}
nav{display:flex;flex-wrap:wrap;gap:22px;margin:24px 0 28px;font-size:14px}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.metric,.panel{border:1px solid var(--line);border-radius:10px;background:#fff}
.metric{padding:20px 22px;border-top:3px solid #377f90}.metric h2{font-size:13px;font-weight:500;color:var(--muted);margin-bottom:6px}
.metric strong{font-size:32px;line-height:1.35;font-weight:700;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.metric small{display:block;font-size:12px;color:var(--muted);margin-top:6px}
.coverage{margin:14px 0 26px;color:var(--muted);font-size:13px}.panel{padding:24px;margin-bottom:20px;min-width:0;scroll-margin-top:20px}
.alert{border-left:4px solid #779198}.alert.warning{border-left-color:#bf522f;background:#fff9f3}.alert.normal{border-left-color:#3b8263;background:#f5fbf7}
.alert pre{font:inherit;font-size:14px;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}
.section-heading .note{margin:0 0 12px}.chart{margin:0}.chart img{display:block;width:100%;height:auto}.chart figcaption{font-size:12px;color:var(--muted);margin:8px 0 0}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.grid .panel{margin-bottom:0}.section-grid{margin-bottom:20px}
.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px 12px;border-bottom:1px solid #e7ecee;text-align:left;overflow-wrap:anywhere}
th{font-size:12px;color:var(--muted);font-weight:600;background:#f8faf9}tbody tr:last-child td{border-bottom:0}
td:not(:first-child){font-variant-numeric:tabular-nums;white-space:nowrap}.empty{background:#f7f9fa;padding:14px;border-radius:6px;color:var(--muted)}
.keyword-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}.positive{color:#095fae}.negative{color:#b43c27}
footer{padding:18px 0 0;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
@media(max-width:720px){.wrap{padding:24px 16px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.grid,.keyword-grid{grid-template-columns:1fr}.panel{padding:18px}.metric{padding:16px}.metric strong{font-size:27px}h1{font-size:27px}.header-top,.section-heading{align-items:flex-start;flex-wrap:wrap}.badge{margin-bottom:12px}}
@media print{body{background:#fff}.wrap{max-width:none;padding:0}nav{display:none}.metric,.alert,figure{break-inside:avoid}.panel{border-color:#bbb}.chart img{max-height:650px;object-fit:contain}.grid{display:block}.grid .panel{margin-bottom:20px}footer{margin-top:20px}}
"""


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    head = "".join(f'<th scope="col">{_text(header)}</th>' for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{_text(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _keywords(title: str, items: Sequence[KeywordCount], css: str) -> str:
    content = (_table(["키워드", "리뷰 수"], [[item.keyword, f"{item.count:,}건"] for item in items[:10]])
               if items else '<p class="empty">집계된 키워드가 없습니다.</p>')
    return f'<section><h3 class="{css}">{title}</h3>{content}</section>'


def render_dashboard_html(
    statistics: ReviewStatistics, charts: Sequence[bytes], *, filters: ReviewFilter,
    generated_at: datetime, sentiment_change: SentimentChangeResult | None = None,
) -> str:
    """Consume shared statistics and PNG bytes; perform no file, DB or API I/O."""
    if not charts or any(not isinstance(chart, bytes) or not chart.startswith(_PNG_SIGNATURE) for chart in charts):
        raise OutputError("HTML 대시보드에 포함할 PNG 차트가 없거나 형식이 올바르지 않습니다.")
    if not isinstance(generated_at, datetime) or generated_at.utcoffset() is None:
        raise ValidationError("HTML 생성 시각은 시간대가 있는 datetime이어야 합니다.")
    timestamp = generated_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    scope = [f"제품: {filters.product_name} (부분 일치)" if filters.product_name else "전체 제품",
             f"기간: {filters.date_from or '시작 제한 없음'} ~ {filters.date_to or '종료 제한 없음'}"]
    if filters.sentiment is not None:
        scope.append("감정: " + dict(_SENTIMENTS)[filters.sentiment])
    if filters.rating is not None:
        scope.append(f"별점: {filters.rating}")
    if filters.rating_min is not None:
        scope.append(f"최소 별점: {filters.rating_min}")
    scope_html = "".join(f"<span>{_text(item)}</span>" for item in scope)
    coverage = statistics.analyzed_reviews / statistics.total_reviews if statistics.total_reviews else 0
    average = f"{statistics.average_rating:.2f}" if statistics.average_rating is not None else "N/A"
    negative = f"{statistics.sentiment_ratios.get(Sentiment.NEGATIVE, 0):.1%}" if statistics.analyzed_reviews else "N/A"
    metrics = (
        ("정제 리뷰", f"{statistics.total_reviews:,}", "선택한 조건의 전체 리뷰"),
        ("분석 완료", f"{statistics.analyzed_reviews:,}", f"분석 완료율 {coverage:.1%}"),
        ("평균 별점", average, "별점이 있는 정제 리뷰 기준 · 5점 만점"),
        ("부정 리뷰 비율", negative, "분석 완료 리뷰 기준"),
    )
    metrics_html = "".join(f'<article class="metric"><h2>{label}</h2><strong>{value}</strong><small>{hint}</small></article>'
                           for label, value, hint in metrics)
    alert_html = ""
    if sentiment_change is not None:
        css = {SentimentChangeStatus.WARNING: "warning", SentimentChangeStatus.NORMAL: "normal"}.get(
            sentiment_change.status, "waiting")
        alert_html = (f'<section class="panel alert {css}" aria-label="감정 변화 알림">'
                      f'<h2>감정 변화 알림</h2><pre>{_text(format_sentiment_change(sentiment_change))}</pre></section>')
    chart_html = "".join(
        f'<figure class="chart"><img src="data:image/png;base64,{base64.b64encode(chart).decode("ascii")}" '
        f'alt="감정 분포, 날짜별 추이, 별점별 분포와 키워드 차트 {index}" decoding="async">'
        f'<figcaption>차트 {index} · 위 필터에 해당하는 저장된 분석 결과</figcaption></figure>'
        for index, chart in enumerate(charts, 1)
    )
    sentiments = _table(["감정", "리뷰 수", "비율"], [
        [label, f"{statistics.sentiment_counts.get(sentiment, 0):,}건",
         f"{statistics.sentiment_ratios.get(sentiment, 0):.1%}"] for sentiment, label in _SENTIMENTS
    ])
    dates = (_table(["리뷰 작성일", "긍정", "중립", "부정"], [
        [day.isoformat(), *(f"{counts.get(sentiment, 0):,}건" for sentiment, _ in _SENTIMENTS)]
        for day, counts in sorted(statistics.daily_sentiment_counts.items())
    ]) if statistics.daily_sentiment_counts else '<p class="empty">분석 완료 리뷰의 날짜별 집계가 없습니다.</p>')
    ratings = _table(["별점", "긍정", "중립", "부정"], [
        [f"{rating}점", *(f"{statistics.rating_sentiment_matrix.get(rating, {}).get(sentiment, 0):,}건"
                         for sentiment, _ in _SENTIMENTS)] for rating in range(1, 6)
    ])
    keywords = (_keywords("긍정 리뷰의 키워드", statistics.top_positive_keywords, "positive")
                + _keywords("부정 리뷰의 키워드", statistics.top_negative_keywords, "negative"))
    empty = '<p class="empty">조건에 맞는 정제 리뷰가 없습니다.</p>' if not statistics.total_reviews else ""
    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>고객 리뷰 대시보드</title>
<style>{_STYLE}</style>
</head>
<body><main class="wrap">
<header>
<div class="header-top"><p class="brand">REVIEW LAB / SNAPSHOT</p><span class="badge">단일 HTML 대시보드</span></div>
<h1>고객 리뷰 대시보드</h1>
<p class="muted">고객의 반응을 숫자와 추이로 살펴보세요.</p>
<div class="scope" aria-label="조회 조건">{scope_html}</div>
<p class="stamp">생성 시각 (UTC) <time datetime="{timestamp}">{timestamp}</time></p>
<nav aria-label="대시보드 목차"><a href="#chart">분석 차트</a><a href="#sentiment">감정 분포</a><a href="#keywords">주요 키워드</a><a href="#details">상세 집계</a></nav>
</header>
{empty}
<section class="metrics" aria-label="주요 지표">{metrics_html}</section>
<p class="coverage">미분석 (실패 제외) {statistics.unanalyzed_reviews:,}건 · 분석 실패 {statistics.failed_reviews:,}건 · 완료율은 처리 현황 지표입니다.</p>
{alert_html}
<section class="panel" id="chart"><div class="section-heading"><h2>분석 차트</h2><p class="note">감정 분포 · 일별 추이 · 별점 · 키워드</p></div>{chart_html}</section>
<section class="panel" id="sentiment"><h2>감정 분포</h2><p class="note">분석 완료 {statistics.analyzed_reviews:,}건 기준</p>{sentiments}</section>
<section class="panel" id="keywords"><h2>주요 키워드</h2><p class="note">키워드를 포함한 리뷰 수 · 감정별 상위 10개. 키워드는 리뷰 전체의 감정별로 묶습니다.</p><div class="keyword-grid">{keywords}</div></section>
<div class="grid section-grid" id="details"><section class="panel"><h2>날짜별 감정 분포</h2>{dates}</section><section class="panel"><h2>별점별 감정 분포</h2>{ratings}</section></div>
<footer><p>생성 시점에 저장된 분석 결과입니다. 최신 내용을 확인하려면 대시보드를 다시 생성하세요.</p><p>이 HTML 파일 하나로 차트와 통계를 열람할 수 있습니다.</p></footer>
</main></body></html>
'''
