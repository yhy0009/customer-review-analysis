"""Offline insight cards with native, keyboard-accessible evidence disclosure."""
from __future__ import annotations

from html import escape
from typing import Sequence

from src.models import InsightResult, KeywordCount, ReviewFilter, Sentiment


INSIGHT_STYLE = """
#insight{scroll-margin-top:20px}
.insight-eyebrow{font-size:11px;letter-spacing:1.6px;font-weight:700;color:#5275ac;margin:0 0 8px}
.insight-summary{font-size:21px;line-height:1.8;letter-spacing:-.4px;font-weight:550;margin:20px 0 16px;white-space:pre-wrap;overflow-wrap:anywhere}
.insight-meta{font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.insight-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:36px;margin:26px 0}
.insight-list{list-style:none;padding:0;margin:0;display:grid;gap:12px}
.insight-list li{display:flex;gap:12px;align-items:baseline;overflow-wrap:anywhere}
.insight-list li>span:last-child{min-width:0;white-space:pre-wrap}
.insight-index{font-size:12px;font-variant-numeric:tabular-nums;color:#5977a5;flex-shrink:0}
.insight-note{padding:12px 14px;border-radius:6px;background:#f5f7fb;color:var(--muted);font-size:13px;overflow-wrap:anywhere}
.insight-evidence{border-top:1px solid var(--line)}
.insight-evidence summary{display:flex;align-items:center;gap:14px;padding:18px 0;cursor:pointer;list-style:none}
.insight-evidence summary::-webkit-details-marker{display:none}
.insight-evidence summary:hover{background:#f7f9fc}
.insight-evidence summary:focus-visible{outline:3px solid #d69c22;outline-offset:3px;border-radius:4px}
.insight-description{display:flex;flex-direction:column;flex:1;min-width:0;overflow-wrap:anywhere}
.insight-product{font-size:12px;color:var(--muted);margin-bottom:3px}
.insight-kind,.insight-count{font-size:12px;white-space:nowrap;flex-shrink:0}
.insight-kind{padding:3px 9px;border-radius:5px;font-weight:600}
.insight-kind.complaints{background:#fff1ed;color:#a74935}.insight-kind.praises{background:#edf7f2;color:#2f7356}
.insight-count{color:var(--muted);font-variant-numeric:tabular-nums}
.insight-chevron{font-size:22px;color:#5977a5;line-height:1;flex-shrink:0}
.insight-evidence[open] .insight-chevron{transform:rotate(45deg)}
.insight-citations{padding:0 0 12px 30px}
.insight-citation{padding:14px 18px;border-left:3px solid #b7c9f8;background:#f7f9fc;border-radius:0 6px 6px 0;margin:0 0 12px}
.insight-citation figcaption{display:flex;flex-wrap:wrap;gap:8px 14px;font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.insight-review{color:#285eaa;font-weight:600}
.insight-citation blockquote{margin:10px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}
.insight-chips{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:8px}
.insight-chips li{padding:5px 10px;border:1px solid var(--line);border-radius:6px;background:#f8fafb;font-size:13px;overflow-wrap:anywhere;max-width:100%}
.insight-chips span{color:var(--muted);margin-left:8px;white-space:nowrap}
@media(max-width:720px){.insight-columns{grid-template-columns:1fr;gap:24px}.insight-summary{font-size:18px}.insight-evidence summary{gap:8px;flex-wrap:wrap}.insight-description{flex-basis:calc(100% - 36px)}.insight-kind{margin-left:26px}.insight-chevron{margin-left:auto}.insight-citations{padding-left:0}.insight-citation{padding:12px}}
@media print{.insight-evidence::details-content{content-visibility:visible}.insight-citation{break-inside:avoid}.insight-chevron{display:none}}
"""


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def _filter_text(filters: ReviewFilter) -> str:
    conditions = [f"제품명 포함: {filters.product_name}" if filters.product_name is not None else "전체 제품"]
    conditions.append(f"기간: {filters.date_from or '시작 제한 없음'} ~ {filters.date_to or '종료 제한 없음'}"
                      if filters.date_from or filters.date_to else "전체 기간")
    labels = {Sentiment.POSITIVE: "긍정", Sentiment.NEUTRAL: "중립", Sentiment.NEGATIVE: "부정"}
    conditions.append(f"감정: {labels[filters.sentiment]}" if filters.sentiment is not None else "전체 감정")
    if filters.rating is not None:
        conditions.append(f"별점: {filters.rating}/5")
    if filters.rating_min is not None:
        conditions.append(f"최소 별점: {filters.rating_min}/5")
    return " · ".join(conditions)


def _issue_list(title: str, items: Sequence[str]) -> str:
    visible = [item for item in items if item.strip()]
    content = ('<ol class="insight-list">' + "".join(
        f'<li><span class="insight-index" aria-hidden="true">{index:02d}</span><span>{_text(item)}</span></li>'
        for index, item in enumerate(visible, 1)) + "</ol>"
        if visible else '<p class="muted">확인된 항목이 없습니다.</p>')
    return f'<section><h3>{title}</h3>{content}</section>'


def _keyword_chips(title: str, items: Sequence[KeywordCount]) -> str:
    content = ('<ul class="insight-chips">' + "".join(
        f'<li>{_text(item.keyword)}<span>{item.count:,}건</span></li>' for item in items) + "</ul>"
        if items else '<p class="muted">집계된 키워드가 없습니다.</p>')
    return f'<section><h3>{title}</h3>{content}</section>'


def render_insight_section(insight: InsightResult, *, analyzed_reviews: int) -> str:
    """Keep all stored evidence and scope metadata; no scripts or remote data."""
    timestamp = insight.generated_at.isoformat().replace("+00:00", "Z")
    summary = (insight.summary.strip() or "요약이 없습니다." if insight.review_count else
               "조건에 맞는 분석 완료 리뷰가 없어 AI 요약을 생성하지 않았습니다.")
    cards = [f'''<article class="panel">
<p class="insight-eyebrow">AI GENERATED INSIGHT</p><h2>주요 인사이트</h2>
<p class="insight-summary">{_text(summary)}</p>
<p class="insight-meta">실제 추출 대상: 분석 완료 리뷰 {insight.review_count:,}건 · 현재 통계의 분석 완료 {analyzed_reviews:,}건</p>
<p class="insight-meta">인사이트 생성 시각 (UTC): <time datetime="{_text(timestamp)}">{_text(timestamp)}</time></p>
<p class="insight-meta">필터: {_text(_filter_text(insight.filters))}</p>
<p class="insight-note">통계와 인사이트의 대상 범위는 서로 다를 수 있습니다. 아래 내용은 표시된 추출 대상에 한정됩니다.</p>''']
    # Match CLI behavior: empty results must not expose stale optional content.
    if insight.review_count:
        if insight.summary_scope == "top_complaints":
            cards.append('<p class="insight-note">요약 범위: 리뷰 수 기준 주요 불편 최대 3개와 장점 최대 1개입니다. '
                         '심각도 순위가 아니며 모든 근거는 아래 목록에 남습니다.</p>')
        cards.append('<div class="insight-columns">' + _issue_list("주요 이슈", insight.issues)
                     + _issue_list("개선 제안", insight.improvement_suggestions) + '</div>')
        cards.append('<p class="note">AI의 해석과 개선 제안입니다. 의미적 정확성이나 개선 효과가 검증된 결론은 아닙니다.</p>')
    cards.append('</article>')
    if insight.review_count:
        cards.append(f'<article class="panel"><div class="section-heading"><h2>전체 원문 근거</h2>'
                     f'<span class="badge">{len(insight.evidence_groups):,}개 주제</span></div>'
                     '<p class="note">제품별 주제를 펼쳐 리뷰 번호와 저장된 원문 인용을 확인하세요.</p>')
        for index, group in enumerate(insight.evidence_groups, 1):
            kind, label = ("complaints", "불편") if group.kind == "complaints" else ("praises", "장점")
            citations = "".join(
                f'<figure class="insight-citation"><figcaption><span class="insight-review">리뷰 {citation.review_id}</span>'
                f'<span>{_text(citation.label)}</span></figcaption><blockquote>{_text(citation.quote)}</blockquote></figure>'
                for citation in group.citations)
            cards.append(f'''<details class="insight-evidence"><summary>
<span class="insight-index">{index:02d}</span><span class="insight-description">
<span class="insight-product">{_text(group.product_name or '제품명 없음')}</span><strong>{_text(group.label)}</strong></span>
<span class="insight-kind {kind}">{label}</span><span class="insight-count">{group.review_count:,}건</span>
<span class="insight-chevron" aria-hidden="true">+</span></summary><div class="insight-citations">{citations}</div></details>''')
        if not insight.evidence_groups:
            cards.append('<p class="empty">별도로 저장된 원문 근거가 없습니다.</p>')
        cards.append('</article><article class="panel"><h2>인사이트 키워드</h2>'
                     '<p class="note">실제 추출 대상에서 키워드를 포함한 리뷰 수입니다.</p><div class="insight-columns">'
                     + _keyword_chips("긍정 키워드", insight.positive_keywords)
                     + _keyword_chips("부정 키워드", insight.negative_keywords) + '</div></article>')
    return '<section id="insight" aria-label="AI 인사이트">' + "".join(cards) + '</section>'
