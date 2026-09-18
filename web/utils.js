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
  config_mismatch: ["현재 AI 설정으로 다시 생성해 주세요", "저장된 결과의 모델·프롬프트·서버 설정이 현재 설정과 다르거나 생성 정보가 없습니다. 위의 생성 버튼으로 다시 준비할 수 있습니다."],
  invalid: ["저장된 인사이트를 읽지 못했어요", "인사이트를 다시 생성하거나 저장 파일을 확인하세요. 통계와 리뷰는 계속 조회할 수 있습니다."],
  missing: ["아직 준비된 인사이트가 없어요", "저장된 분석으로 인사이트 파일을 준비하면 이곳에서 요약과 원문 근거를 확인할 수 있습니다."],
  scope_mismatch: ["다른 조회 조건의 인사이트가 있어요", "현재 필터와 인사이트의 조건이 달라 표시하지 않습니다. 조건을 초기화하거나 현재 범위의 인사이트를 준비하세요."],
  stale: ["인사이트를 다시 준비해 주세요", "리뷰나 분석 결과가 생성 당시와 달라졌습니다. 현재 데이터와 일치하는 인사이트가 필요합니다."],
};

export function provenanceText(profile) {
  if (!profile) return "생성 정보가 없는 이전 결과입니다.";
  const reasoning = profile.reasoning_effort ? ` · 추론 ${profile.reasoning_effort}` : "";
  return `요청 모델 ${profile.model} · ${profile.provider} · 프롬프트 ${profile.prompt_version}${reasoning}`;
}

export function generationView(generation, insightStatus) {
  if (!generation?.enabled) return {disabled: true, label: "생성 비활성", message: "현재는 저장된 인사이트를 조회합니다."};
  if (generation.job?.status === "running") return {disabled: true, label: "생성 중…", message: `분석 완료 리뷰 ${number(generation.job.review_count)}건으로 생성 중입니다. 다른 조건을 조회해도 작업은 계속됩니다.`};
  if (insightStatus === "available") return {disabled: true, label: "저장된 결과 사용 중", message: "현재 데이터와 일치하는 인사이트를 불러왔습니다. 추가 AI 호출은 하지 않습니다."};
  if (!generation.review_count) return {disabled: true, label: "생성 대상 없음", message: "현재 조건에 분석 완료 리뷰가 없습니다."};
  if (generation.job?.status === "failed") {
    const needsCheck = ["check_settings", "check_storage", "change_scope"].includes(generation.job.retry_action);
    return {disabled: false, label: needsCheck ? "확인 후 다시 생성" : "다시 생성",
      message: `${generation.job.error || "인사이트 생성에 실패했습니다. 다시 시도하세요."} 다시 생성하면 AI를 새로 호출하며 사용량이 발생할 수 있습니다.`};
  }
  return {disabled: false, label: "현재 조건으로 인사이트 생성", message: `분석 완료 리뷰 ${number(generation.review_count)}건 · 최대 ${number(generation.limit)}건. 버튼을 누르면 설정된 AI로 리뷰를 전송하며 사용량이 발생할 수 있습니다.`};
}
