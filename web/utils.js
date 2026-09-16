export const labels = {positive: "긍정", neutral: "중립", negative: "부정"};
export const number = value => new Intl.NumberFormat("ko-KR").format(value);
export const percent = value => new Intl.NumberFormat("ko-KR", {style: "percent", maximumFractionDigits: 1}).format(value);
export function dateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "시각 정보 없음" : new Intl.DateTimeFormat("ko-KR", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}).format(date);
}
export function queryString(filters, page = 1) {
  const params = new URLSearchParams();
  for (const key of ["product_name", "date_from", "date_to", "sentiment"]) {
    if (filters[key]) params.set(key, filters[key]);
  }
  params.set("page", String(page));
  return params.toString();
}
export function scopeText(filters) {
  const parts = [filters.product_name ? `제품명 포함: ${filters.product_name}` : "전체 제품"];
  if (filters.date_from || filters.date_to) parts.push(`${filters.date_from || "시작 제한 없음"} ~ ${filters.date_to || "종료 제한 없음"}`);
  else parts.push("전체 기간");
  parts.push(labels[filters.sentiment] || "전체 감정");
  return parts.join(" · ");
}
export const insightStates = {
  missing: ["아직 준비된 인사이트가 없어요", "저장된 분석으로 인사이트 파일을 준비하면 이곳에서 요약과 원문 근거를 확인할 수 있습니다."],
  scope_mismatch: ["다른 조회 조건의 인사이트가 있어요", "현재 필터와 인사이트의 조건이 달라 표시하지 않습니다. 조건을 초기화하거나 현재 범위의 인사이트를 준비하세요."],
  stale: ["인사이트를 다시 준비해 주세요", "리뷰나 분석 결과가 생성 당시와 달라졌습니다. 현재 데이터와 일치하는 인사이트가 필요합니다."],
};
