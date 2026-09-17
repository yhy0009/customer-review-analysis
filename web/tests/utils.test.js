import {test} from "node:test";
import assert from "node:assert/strict";
import {queryString, scopeText, percent, dateTime, insightStates} from "../utils.js";

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
  assert.equal(Object.keys(insightStates).length, 3);
  assert.notDeepEqual(insightStates.missing, insightStates.stale);
  assert.match(insightStates.scope_mismatch[1], /현재 필터/);
});
