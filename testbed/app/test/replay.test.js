import { test } from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import { egressProbe, runReplay, urlOk } from '../src/replay.js';

// A fake clock: sleep advances time instantly, so a 4-hour replay runs in milliseconds.
function clock(startMs) {
  let t = startMs;
  return { now: () => t, sleep: async (ms) => { t += ms; }, advance: (ms) => { t += ms; } };
}

// One recorded event per second, data = its index.
function recording(n, firstIso = '2026-10-12T10:00:00.000Z') {
  const t0 = Date.parse(firstIso);
  return Array.from({ length: n }, (_, i) => ({ recv_ts: new Date(t0 + i * 1000).toISOString(), data: i }));
}

async function run(records, opts) {
  const emitted = [], dropped = [];
  const stats = await runReplay(records, {
    speed: 1, available: () => true,
    emit: async (d) => emitted.push([d, opts.now()]),
    drop: (d) => dropped.push(d),
    ...opts,
  });
  return { emitted, dropped, stats };
}

test('a replay started on time delivers every event at its recorded offset from SESSION_START', async () => {
  const c = clock(1_000_000);
  const { emitted, stats } = await run(recording(5), { startMs: 1_000_000, ...c });
  assert.deepEqual(emitted, [[0, 1_000_000], [1, 1_001_000], [2, 1_002_000], [3, 1_003_000], [4, 1_004_000]]);
  assert.equal(stats.skipped, 0);
});

test('a collector (re)started mid-session seeks to now: the traffic after the restart is unchanged', async () => {
  const start = 1_000_000;
  const full = await run(recording(10), { startMs: start, ...clock(start) });
  // restart 4.5 s into the session: events 0-4 are in the past
  const late = await run(recording(10), { startMs: start, ...clock(start + 4500) });
  assert.equal(late.stats.skipped, 5);
  assert.deepEqual(late.emitted, full.emitted.slice(5));
});

test('a replay started before SESSION_START waits for it', async () => {
  const c = clock(990_000);
  const { emitted } = await run(recording(2), { startMs: 1_000_000, ...c });
  assert.deepEqual(emitted, [[0, 1_000_000], [1, 1_001_000]]);
});

test('after the seek, a slow event is delivered late rather than skipped', async () => {
  const c = clock(1_000_000);
  const emitted = [];
  await runReplay(recording(3), {
    startMs: 1_000_000, speed: 1, now: c.now, sleep: c.sleep, available: () => true,
    emit: async (d) => { emitted.push(d); if (d === 0) c.advance(5000); },   // Redis stalls 5 s on the first
    drop: () => {},
  });
  assert.deepEqual(emitted, [0, 1, 2]);
});

test('events are dropped, not delayed, while the source is unavailable', async () => {
  const c = clock(1_000_000);
  const down = (t) => t >= 1_002_000 && t < 1_004_000;   // e.g. upstream_cut for 2 s
  const { emitted, dropped, stats } = await run(recording(6), {
    startMs: 1_000_000, ...c, available: () => !down(c.now()),
  });
  assert.deepEqual(emitted.map(([d]) => d), [0, 1, 4, 5]);
  assert.deepEqual(dropped, [2, 3]);
  assert.equal(stats.dropped, 2);
  // the outage does not shift what comes after it
  assert.deepEqual(emitted.slice(2), [[4, 1_004_000], [5, 1_005_000]]);
});

test('REPLAY_SPEED compresses time', async () => {
  const c = clock(0);
  const { emitted } = await run(recording(3), { startMs: 0, speed: 2, ...c });
  assert.deepEqual(emitted, [[0, 0], [1, 500], [2, 1000]]);
});

test('only the canonical stream path counts as a working URL (bad_config_collector)', () => {
  assert.equal(urlOk('https://stream.wikimedia.org/v2/stream/recentchange'), true);
  assert.equal(urlOk('https://stream.wikimedia.org/v2/stream/recentchanges'), false);
  assert.equal(urlOk('not a url'), false);
});

test('the egress probe reports a listening port as up and a closed one as down', async () => {
  const srv = net.createServer((s) => { s.on('error', () => {}); s.end('ok\n'); });
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  const { port } = srv.address();
  const up = egressProbe({ host: '127.0.0.1', port, intervalMs: 20, timeoutMs: 200 });
  await new Promise((r) => setTimeout(r, 100));
  assert.equal(up.up(), true);
  await new Promise((r) => srv.close(r));
  await new Promise((r) => setTimeout(r, 150));
  assert.equal(up.up(), false);
  up.stop();
});
