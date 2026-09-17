import {labels, number, percent, dateTime, queryString, scopeText, insightStates, generationView, provenanceText} from "./utils.js";

const $ = selector => document.querySelector(selector);
const state = {filters: {}, page: 1, snapshot: null, view: "overview", controller: null, request: 0, reviewRequest: 0};
const titles = {
  overview: ["분석 개요", "고객의 목소리를 한눈에", "리뷰의 흐름을 읽고, 다음 개선점을 발견하세요."],
  reviews: ["리뷰 탐색", "한 문장 뒤에 있는 고객 경험", "저장된 리뷰와 분석 결과를 함께 살펴보세요."],
  insights: ["AI 인사이트", "숫자에서 발견한 다음 행동", "주요 이슈와 개선 제안을 원문 근거로 확인하세요."],
};
function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}
function badge(sentiment) {
  return el("span", `sentiment-badge ${sentiment || "unanalyzed"}`, labels[sentiment] || "분석 대기·실패");
}
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { $("#toast").hidden = true; }, 5000);
}
async function readJSON(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "자료를 불러오지 못했습니다.");
  return body;
}
function updateNavigation() {
  state.view = Object.hasOwn(titles, location.hash.slice(1)) ? location.hash.slice(1) : "overview";
  const [name, title, description] = titles[state.view];
  $("#page-title").textContent = title;
  $("#page-description").textContent = description;
  $("#breadcrumb-current").textContent = name;
  document.title = `${name} · Review Lab`;
  document.querySelectorAll("[data-view]").forEach(link => {
    if (link.dataset.view === state.view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  document.querySelectorAll(".view-panel").forEach(panel => { panel.hidden = panel.id !== `${state.view}-view`; });
}
function renderKeywords(selector, items) {
  const host = $(selector);
  host.replaceChildren();
  if (!items.length) host.append(el("p", "muted", "집계된 키워드가 없습니다."));
  for (const item of items) {
    const chip = el("span", "keyword-chip");
    chip.append(el("span", "", item.keyword), el("strong", "", number(item.count)));
    host.append(chip);
  }
}
function issueList(title, items, className = "issue-list") {
  const section = el("section", "insight-section");
  section.append(el("h3", "", title));
  const list = el("ul", className);
  items.forEach((item, index) => {
    const row = el("li");
    row.append(el("span", "issue-number", String(index + 1).padStart(2, "0")), el("span", "", item));
    list.append(row);
  });
  if (!items.length) list.append(el("li", "muted", "확인된 항목이 없습니다."));
  section.append(list);
  return section;
}
function unavailableInsight(status, canGenerate = false) {
  const [title, description] = insightStates[status] || insightStates.missing;
  const node = el("div", "empty-state insight-empty");
  node.append(el("div", "empty-symbol", "✧"), el("h3", "", title), el("p", "", canGenerate && status !== "config_mismatch"
    ? "화면 위의 생성 버튼으로 현재 조건의 인사이트를 준비하세요. 진행 상태도 같은 곳에서 확인할 수 있습니다."
    : description));
  return node;
}
function renderInsights(data) {
  const preview = $("#insight-preview-content"), full = $("#insight-full");
  preview.replaceChildren(); full.replaceChildren();
  const insight = data.insight;
  if (!insight) {
    preview.append(unavailableInsight(data.insight_status, data.generation?.enabled));
    const panel = el("article", "panel"); panel.append(unavailableInsight(data.insight_status, data.generation?.enabled)); full.append(panel);
    return;
  }
  const summary = insight.review_count ? insight.summary : "조건에 맞는 분석 완료 리뷰가 없어 AI 요약을 생성하지 않았습니다.";
  preview.append(el("span", "insight-scope", `분석 리뷰 ${number(insight.review_count)}건의 인사이트`),
    el("p", "provenance-note", provenanceText(data.insight_provenance)),
    el("p", "summary-text", summary), issueList("주요 이슈", insight.issues));
  if (insight.summary_scope === "top_complaints") preview.append(el("p", "scope-note", "리뷰 수 기준 불편 최대 3개 · 전체 근거는 상세 화면에 보존됩니다."));
  const summaryPanel = el("article", "panel full-summary");
  summaryPanel.append(el("p", "eyebrow", "AI GENERATED INSIGHT"), el("h2", "", "주요 인사이트"),
    el("p", "provenance-note", provenanceText(data.insight_provenance)),
    el("p", "summary-large", summary),
    el("p", "muted", `${number(insight.review_count)}건 / 현재 분석 완료 ${number(data.statistics.analyzed_reviews)}건 · 생성 ${dateTime(insight.generated_at)}`));
  if (insight.summary_scope === "top_complaints") summaryPanel.append(el("p", "scope-note", "리뷰 수 기준 주요 불편 최대 3개와 장점 최대 1개의 요약입니다. 심각도 순위가 아니며 모든 근거는 아래 목록에 남습니다."));
  const columns = el("div", "insight-columns");
  columns.append(issueList("주요 이슈", insight.issues), issueList("개선 제안", insight.improvement_suggestions, "issue-list suggestions"));
  summaryPanel.append(columns, el("p", "scope-note", "AI의 해석과 개선 제안입니다. 의미적 정확성이나 개선 효과가 검증된 결론은 아닙니다."));
  full.append(summaryPanel);
  const evidencePanel = el("article", "panel evidence-panel");
  const groups = insight.evidence_groups || [];
  const heading = el("div", "panel-heading");
  heading.append(el("h2", "", "전체 원문 근거"), el("span", "tag", `${number(groups.length)}개 주제`));
  evidencePanel.append(heading, el("p", "evidence-help", "제품별 주제를 펼쳐 인용을 확인하세요. 리뷰 번호를 선택하면 전체 원문을 볼 수 있습니다."));
  groups.forEach((group, index) => {
    const details = el("details", "evidence-group");
    const trigger = el("summary");
    const description = el("div", "evidence-description");
    description.append(el("span", "evidence-product", group.product_name), el("strong", "", group.label));
    trigger.append(el("span", "evidence-index", String(index + 1).padStart(2, "0")), description,
      el("span", `finding-kind ${group.kind}`, group.kind === "complaints" ? "불편" : "장점"),
      el("span", "evidence-count", `${new Set(group.citations.map(c => c.review_id)).size}건`), el("span", "chevron", "+"));
    const citations = el("div", "citations");
    group.citations.forEach(citation => {
      const box = el("div", "citation");
      const button = el("button", "text-button", `리뷰 #${citation.review_id} ↗`);
      button.type = "button"; button.dataset.review = citation.review_id;
      box.append(button, el("span", "citation-label", citation.label), el("blockquote", "", citation.quote));
      citations.append(box);
    });
    details.append(trigger, citations); evidencePanel.append(details);
  });
  if (!groups.length) evidencePanel.append(el("p", "empty-state", "별도로 저장된 원문 근거가 없습니다."));
  full.append(evidencePanel);
}
function renderReviews(data) {
  const host = $("#review-rows"); host.replaceChildren();
  const page = data.page;
  for (const review of page.items) {
    const tr = el("tr");
    const product = el("td", "product-cell");
    product.append(el("strong", "", review.product_name), el("small", "", review.review_date));
    const text = el("td", "review-text-cell"); text.append(el("p", "review-excerpt", review.review_text));
    const rating = el("td", "rating-cell", `★ ${review.rating}`);
    const sentiment = el("td"); sentiment.append(badge(review.analysis?.sentiment));
    const action = el("td"); const button = el("button", "text-button", "상세 ↗");
    button.type = "button"; button.dataset.review = review.id; button.setAttribute("aria-label", `리뷰 ${review.id} 상세 보기`);
    action.append(button); tr.append(product, text, rating, sentiment, action); host.append(tr);
  }
  if (!page.items.length) {
    const row = el("tr"), cell = el("td", "table-empty", "조건에 맞는 리뷰가 없습니다. 필터를 조정해 보세요.");
    cell.colSpan = 5; row.append(cell); host.append(row);
  }
  $("#review-count").textContent = `${number(page.total_items)}건`;
  $("#page-info").textContent = page.total_pages ? `${number(page.number)} / ${number(page.total_pages)} 페이지 · 페이지당 ${page.size}건` : "0건";
  $("#previous-page").disabled = page.number <= 1;
  $("#next-page").disabled = page.number >= page.total_pages;
}
function renderChart(data) {
  const image = $("#chart");
  image.hidden = true; $("#chart-error").hidden = true; $("#chart-loading").hidden = false;
  $("#chart-open").hidden = true;
  image.onload = () => {
    if (state.snapshot?.snapshot_id !== data.snapshot_id) return;
    $("#chart-loading").hidden = true; image.hidden = false; $("#chart-open").hidden = false;
  };
  image.onerror = () => {
    if (state.snapshot?.snapshot_id !== data.snapshot_id) return;
    $("#chart-loading").hidden = true; $("#chart-error").hidden = false;
  };
  image.src = data.chart_url;
  $("#chart-open").href = data.chart_url;
}
function render(data) {
  const stats = data.statistics;
  $("#data-mode").textContent = data.demo ? "합성 데이터 데모" : "SQLite 데이터";
  $("#data-mode").classList.toggle("demo", data.demo);
  $("#scope-text").textContent = scopeText(data.filters);
  $("#updated-at").textContent = `조회 ${dateTime(data.generated_at)}`;
  $("#nav-count").textContent = number(stats.total_reviews);
  $("#stat-total").textContent = number(stats.total_reviews);
  $("#stat-analyzed").textContent = number(stats.analyzed_reviews);
  $("#stat-analyzed-note").textContent = `미분석 ${number(stats.unanalyzed_reviews)}건 · 실패 ${number(stats.failed_reviews)}건`;
  $("#stat-positive").textContent = stats.analyzed_reviews ? percent(stats.sentiment_ratios.positive || 0) : "—";
  $("#stat-positive-note").textContent = `분석 완료 ${number(stats.analyzed_reviews)}건 중 긍정 ${number(stats.sentiment_counts.positive || 0)}건`;
  $("#stat-rating").textContent = stats.average_rating === null ? "—" : Number(stats.average_rating).toFixed(2);
  renderKeywords("#positive-keywords", stats.top_positive_keywords);
  renderKeywords("#negative-keywords", stats.top_negative_keywords);
  renderInsights(data); renderReviews(data); renderChart(data); updateNavigation(); renderGeneration(data);
}

let generationTimer;
function renderGeneration(data) {
  const generation = data.generation;
  $("#generation-panel").hidden = !generation?.enabled;
  if (!generation?.enabled) return;
  const view = generationView(generation, data.insight_status);
  $("#generation-profile").textContent = generation.profile ? provenanceText(generation.profile) : "";
  $("#generate-insight").disabled = view.disabled;
  $("#generate-insight").textContent = view.label;
  $("#generation-status").textContent = view.message;
  clearTimeout(generationTimer);
  if (generation.job?.status === "running") {
    generationTimer = setTimeout(() => pollGeneration(data), 1200);
  }
}
async function pollGeneration(data) {
  if (state.snapshot !== data) return;
  try {
    const job = await readJSON(`/api/insight-jobs/${data.generation.job.id}`);
    if (state.snapshot !== data) return;
    data.generation.job = job;
    if (job.status === "succeeded") {
      await load();
      toast("인사이트가 저장됐습니다. 현재 조회 데이터와 일치하는 결과를 표시합니다.");
    } else renderGeneration(data);
  } catch (error) {
    if (state.snapshot === data) $("#generation-status").textContent = `${error.message} 새로고침으로 작업 상태를 다시 확인하세요.`;
  }
}
$("#generate-insight").addEventListener("click", async () => {
  const data = state.snapshot;
  if (!data?.generation?.enabled) return;
  $("#generate-insight").disabled = true;
  $("#generation-status").textContent = "생성 요청을 확인하는 중입니다.";
  try {
    const job = await readJSON("/api/insight-jobs", {method: "POST",
      headers: {"Content-Type": "application/json", "X-Dashboard-Token": data.generation.token},
      body: JSON.stringify({snapshot_id: data.snapshot_id})});
    if (state.snapshot !== data) return;
    data.generation.job = job;
    if (job.status === "succeeded") await load();
    else renderGeneration(data);
  } catch (error) {
    if (state.snapshot !== data) return;
    renderGeneration(data);
    $("#generation-status").textContent = error.message;
  }
});
async function load() {
  clearTimeout(generationTimer);
  state.controller?.abort();
  state.controller = new AbortController();
  const request = ++state.request;
  state.snapshot = null;
  $("#review-dialog").close(); $("#download-menu").open = false;
  $("#error").hidden = true; $("#content").hidden = true; $("#loading").hidden = false;
  $("#main").setAttribute("aria-busy", "true");
  try {
    const data = await readJSON(`/api/snapshot?${queryString(state.filters, state.page)}`, {signal: state.controller.signal});
    if (request !== state.request) return;
    if (data.schema_version !== 1) throw new Error("지원하지 않는 대시보드 데이터 버전입니다.");
    state.snapshot = data;
    render(data);
    $("#content").hidden = false;
  } catch (error) {
    if (error.name === "AbortError" || request !== state.request) return;
    $("#error").textContent = `${error.message} 새로고침으로 다시 시도할 수 있습니다.`;
    $("#error").hidden = false;
    $("#data-mode").textContent = "연결 확인 필요";
  } finally {
    if (request === state.request) { $("#loading").hidden = true; $("#main").removeAttribute("aria-busy"); }
  }
}
async function openReview(id) {
  const snapshot = state.snapshot;
  if (!snapshot) return;
  const request = ++state.reviewRequest;
  const dialog = $("#review-dialog"); dialog.showModal();
  $("#review-title").textContent = `리뷰 #${id}`;
  $("#review-detail").replaceChildren(el("p", "muted", "리뷰를 불러오는 중입니다."));
  try {
    const review = await readJSON(`/api/review/${snapshot.snapshot_id}/${id}`);
    if (request !== state.reviewRequest || state.snapshot !== snapshot || !dialog.open) return;
    $("#review-title").textContent = `리뷰 #${review.id}`;
    const content = $("#review-detail"); content.replaceChildren();
    content.append(el("h3", "", review.product_name), el("p", "muted", `${review.review_date} · 별점 ${review.rating}/5`),
      badge(review.analysis?.sentiment), el("h3", "detail-label", "리뷰 원문"), el("p", "original-review", review.review_text));
    if (review.analysis) {
      content.append(el("h3", "detail-label", "저장된 분석"), el("p", "", review.analysis.summary || "저장된 개별 요약이 없습니다."),
        el("p", "muted", `모델 ${review.analysis.model} · 분석 ${dateTime(review.analysis.analyzed_at)}`),
        el("p", "scope-note", `모델 자기평가 신뢰도 ${percent(review.analysis.confidence)} · 검증된 정답 확률이 아닙니다.`));
      const keywords = el("div", "keyword-list");
      review.analysis.keywords.forEach(word => keywords.append(el("span", "keyword-chip", word)));
      content.append(keywords);
    } else content.append(el("p", "scope-note", "저장된 분석 결과가 없습니다. 미분석 또는 분석 실패 상태입니다."));
  } catch (error) {
    if (request === state.reviewRequest && state.snapshot === snapshot && dialog.open) $("#review-detail").replaceChildren(el("p", "error-text", error.message));
  }
}

$("#filters").addEventListener("submit", event => {
  event.preventDefault();
  const next = Object.fromEntries(new FormData(event.currentTarget));
  if (next.date_from && next.date_to && next.date_from > next.date_to) { toast("시작일은 종료일보다 늦을 수 없습니다."); return; }
  state.filters = next; state.page = 1; load();
});
$("#filters").addEventListener("reset", () => { state.filters = {}; state.page = 1; load(); });
$("#refresh").addEventListener("click", load);
$("#retry-chart").addEventListener("click", () => { if (state.snapshot) renderChart(state.snapshot); });
$("#previous-page").addEventListener("click", () => { if (state.page > 1) { state.page--; load(); } });
$("#next-page").addEventListener("click", () => { if (state.snapshot && state.page < state.snapshot.page.total_pages) { state.page++; load(); } });
$("#close-dialog").addEventListener("click", () => $("#review-dialog").close());
document.addEventListener("click", event => {
  const button = event.target.closest("[data-review]");
  if (button) openReview(Number(button.dataset.review));
});
document.querySelectorAll("[data-report]").forEach(button => button.addEventListener("click", async () => {
  const data = state.snapshot, format = button.dataset.report;
  $("#download-menu").open = false;
  if (!data) { toast("먼저 조회 결과를 불러와 주세요."); return; }
  try {
    const response = await fetch(data.report_urls[format]);
    if (!response.ok) throw new Error((await response.json()).error);
    const url = URL.createObjectURL(await response.blob());
    const anchor = el("a"); anchor.href = url; anchor.download = `review-report.${format}`;
    document.body.append(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("현재 조회 결과의 리포트를 다운로드했습니다.");
  } catch (error) { toast(error.message || "리포트를 다운로드하지 못했습니다."); }
}));
window.addEventListener("hashchange", updateNavigation);
updateNavigation(); load();
