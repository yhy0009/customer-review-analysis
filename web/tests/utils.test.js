import {test} from "node:test";
import assert from "node:assert/strict";
import {queryString, scopeText, percent, dateTime, insightStates, generationView} from "../utils.js";

test("query serialization keeps product text literal and drops unknown fields", () => {
  const query = new URLSearchParams(queryString({product_name: "이어폰 & sentiment=negative <script>", secret: "hidden"}, 2));
  assert.equal(query.get("product_name"), "이어폰 & sentiment=negative <script>");
  assert.equal(query.get("page"), "2");
  assert.equal(query.has("sentiment"), false);
  assert.equal(query.has("secret"), false);
});
test("empty filters reset to all-scope and page one", () => {
  assert.equal(queryString({}), "page=1");
  assert.equal(scopeText({}), "전체 제품 · 전체 기간 · 전체 감정");
  assert.match(scopeText({date_to: "2026-09-16", sentiment: "negative"}), /시작 제한 없음 ~ 2026-09-16 · 부정/);
});
test("zero values and missing dates are explicit", () => {
  assert.equal(percent(0), "0%");
  assert.equal(dateTime("invalid"), "시각 정보 없음");
});
test("missing, stale and different-scope insights have distinct guidance", () => {
  assert.equal(Object.keys(insightStates).length, 4);
  assert.notDeepEqual(insightStates.missing, insightStates.stale);
  assert.match(insightStates.scope_mismatch[1], /현재 필터/);
});
test("generation requires explicit action and does not offer duplicate calls", () => {
  const data = {enabled: true, review_count: 6, limit: 50};
  assert.equal(generationView(data, "missing").disabled, false);
  assert.match(generationView(data, "missing").message, /6건/);
  assert.equal(generationView(data, "available").disabled, true);
  assert.equal(generationView({...data, review_count: 0}, "missing").disabled, true);
  assert.equal(generationView({...data, enabled: false}, "missing").disabled, true);
  assert.equal(generationView({...data, job: {status: "running", review_count: 6}}, "missing").disabled, true);
  assert.equal(generationView({...data, job: {status: "failed", error: "실패"}}, "stale").label, "다시 생성");
});
