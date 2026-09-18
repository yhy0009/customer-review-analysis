import {test} from 'node:test';
import assert from 'node:assert/strict';
import {queryString, readQuery, formFilters, ratingSelection, scopeText} from '../utils.js';

test('saved query round trips literal product, dates, sentiment, ratings and page', () => {
  for (const rating of [{rating: '2'}, {rating_min: '4'}]) {
    const filters = {product_name: '이어폰 & #제품 + 50%', date_from: '2026-09-01',
      date_to: '2026-09-18', sentiment: 'negative', ...rating};
    assert.deepEqual(readQuery(queryString(filters, 2)), {filters, page: 2});
    assert.match(scopeText(filters), rating.rating ? /별점 2점$/ : /별점 4점 이상$/);
  }
});

test('form can replace or remove a rating without retaining the other mode', () => {
  for (const rating_filter of ['rating:1', 'rating:5', 'rating_min:1', 'rating_min:5', '']) {
    const filters = formFilters({product_name: '제품', rating_filter, rating: '3', rating_min: '4'});
    assert.equal(ratingSelection(filters), rating_filter);
    assert.equal(Boolean(filters.rating && filters.rating_min), false);
    assert.deepEqual(readQuery(queryString(filters)).filters, filters);
  }
  assert.deepEqual(formFilters({}), {});
});

test('invalid or ambiguous bookmarks fail rather than silently querying all reviews', () => {
  for (const query of ['rating=0', 'rating=6', 'rating=2.0', 'rating_min=-1',
    'rating=1&rating_min=2', 'rating=1&rating=2', 'page=1&page=2', 'page=0', 'page=-1',
    'page=2.5', 'page=', 'page=1000001', 'page=9007199254740992', 'sentiment=toString',
    'date_from=2026-02-30', 'date_from=0000-01-01', 'date_from=2026-13-01',
    'date_from=2026-09-18&date_to=2026-09-01', 'product_name=' + '가'.repeat(201)]) {
    assert.throws(() => readQuery(query), /조회 조건/, query);
  }
  assert.throws(() => formFilters({rating_filter: 'page:4'}), /별점/);
});

test('empty query and unknown parameters cannot introduce extra request fields', () => {
  assert.deepEqual(readQuery(''), {filters: {}, page: 1});
  assert.deepEqual(readQuery('token=secret&__proto__=bad&view=reviews&rating=3'),
    {filters: {rating: '3'}, page: 1});
  assert.deepEqual(readQuery('date_from=2024-02-29'), {filters: {date_from: '2024-02-29'}, page: 1});
});
