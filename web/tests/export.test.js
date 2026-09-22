import {test} from 'node:test';
import assert from 'node:assert/strict';
import {exportView} from '../utils.js';

test('review downloads describe the entire filtered snapshot, not a visible page', () => {
  const view = exportView({status:'available', row_count:18, limit:2000, urls:{csv:'/csv',jsonl:'/jsonl'}});
  assert.equal(view.disabled, false);
  assert.match(view.message, /전체 18건/);
  assert.match(view.message, /조회 당시/);
});

test('large or empty selections and old server responses cannot start downloads', () => {
  const limited = exportView({status:'too_large',row_count:2001,limit:2000,urls:{}});
  assert.equal(limited.disabled, true);
  assert.match(limited.message, /2,001건/);
  assert.match(limited.message, /필터/);
  assert.equal(exportView({status:'available',row_count:0,limit:2000,urls:{}}).disabled, true);
  assert.equal(exportView({status:'available',row_count:18,limit:2000}).disabled, true);
  assert.equal(exportView(undefined).disabled, true);
});
