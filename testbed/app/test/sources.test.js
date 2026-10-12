import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createSSEParser, normalizeWikimedia } from '../src/sources.js';

test('SSE parser handles events split across chunks, comments and ids', () => {
  const feed = createSSEParser();
  assert.deepEqual(feed(':ok\n\nid: [{"p":1}]\nevent: mess'), []);
  const out = feed('age\ndata: {"a":1}\n\ndata: x\ndata: y\n\n');
  assert.equal(out.length, 2);
  assert.deepEqual(out[0], { id: '[{"p":1}]', event: 'message', data: '{"a":1}' });
  assert.equal(out[1].data, 'x\ny');
});

test('Wikimedia events are normalised; canaries are skipped', () => {
  const e = normalizeWikimedia({ meta: { id: 'abc', dt: '2026-10-12T10:00:00Z', domain: 'en.wikipedia.org' },
    type: 'edit', wiki: 'enwiki', title: 'Cairo', bot: false, length: { old: 100, new: 130 } });
  assert.deepEqual(e, { source: 'wikipedia', event_id: 'abc', ts: '2026-10-12T10:00:00Z', kind: 'edit',
    wiki: 'enwiki', title: 'Cairo', bot: false, delta: 30 });
  assert.equal(normalizeWikimedia({ meta: { domain: 'canary' } }), null);
  assert.equal(normalizeWikimedia({ meta: { id: 'n' }, type: 'log', wiki: 'x' }).delta, null);
});
