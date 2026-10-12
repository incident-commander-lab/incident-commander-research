// Worker: consumes the Redis stream in a consumer group, writes events to Postgres in
// batches, and acknowledges them. Lag (events waiting) is exported for alerting.
import os from 'node:os';
import { cfg, log, metrics, pgPool, redis, serveMetrics, sleep } from './common.js';

const BATCH = Number(process.env.BATCH_SIZE || 200);
const CONSUMER = process.env.CONSUMER_NAME || os.hostname();

const processed = new metrics.Counter({ name: 'pulse_events_processed_total', help: 'Events stored in Postgres', labelNames: ['source'] });
const dbErrors = new metrics.Counter({ name: 'pulse_db_errors_total', help: 'Failed Postgres writes' });
const batchSeconds = new metrics.Histogram({ name: 'pulse_batch_seconds', help: 'Time to store one batch', buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10] });
const lag = new metrics.Gauge({ name: 'pulse_stream_lag', help: 'Entries in the stream not yet delivered to the group' });
const pending = new metrics.Gauge({ name: 'pulse_stream_pending', help: 'Entries delivered but not acknowledged' });

let lastOk = Date.now();
serveMetrics(cfg.metricsPort, () => Date.now() - lastOk < 120000);

const r = await redis();
const pool = pgPool();

try {
  await r.xGroupCreate(cfg.stream, cfg.group, '0', { MKSTREAM: true });
} catch (e) {
  if (!String(e.message).includes('BUSYGROUP')) throw e;   // group already exists
}

// Lag sampler, separate from the hot loop.
setInterval(async () => {
  try {
    const groups = await r.xInfoGroups(cfg.stream);
    const g = groups.find((x) => x.name === cfg.group);
    if (g) {
      if (g.lag != null) lag.set(Number(g.lag));
      pending.set(Number(g.pending));
    }
  } catch (e) {
    log('warn', 'lag sample failed', { error: e.message });
  }
}, 5000);

async function store(entries) {
  const rows = entries.map(({ message: m }) => [m.event_id, m.ts, m.source, m.kind, m.wiki, m.title,
    m.bot === 'true', m.delta === '' ? null : Number(m.delta)]);
  const values = rows.map((_, i) => `($${i * 8 + 1},$${i * 8 + 2},$${i * 8 + 3},$${i * 8 + 4},$${i * 8 + 5},$${i * 8 + 6},$${i * 8 + 7},$${i * 8 + 8})`).join(',');
  await pool.query(
    `INSERT INTO edits (event_id, ts, source, kind, wiki, title, bot, delta) VALUES ${values} ON CONFLICT (event_id) DO NOTHING`,
    rows.flat());
}

log('info', 'worker started', { consumer: CONSUMER, batch: BATCH });
for (;;) {
  let res;
  try {
    res = await r.xReadGroup(cfg.group, CONSUMER, [{ key: cfg.stream, id: '>' }], { COUNT: BATCH, BLOCK: 5000 });
  } catch (e) {
    log('error', 'read failed', { error: e.message });
    await sleep(1000);
    continue;
  }
  const entries = res?.[0]?.messages ?? [];
  if (!entries.length) { lastOk = Date.now(); continue; }
  const end = batchSeconds.startTimer();
  try {
    await store(entries);
    await r.xAck(cfg.stream, cfg.group, entries.map((e) => e.id));
    for (const e of entries) processed.inc({ source: e.message.source || 'unknown' });
    lastOk = Date.now();
  } catch (e) {
    dbErrors.inc();
    log('error', 'store failed', { error: e.message, batch: entries.length });
    await sleep(1000);   // unacknowledged entries stay pending; XAUTOCLAIM below picks them up
  } finally {
    end();
  }
  // Recover entries left pending for > 60 s (e.g. after a failed batch or a dead consumer).
  try {
    const claimed = await r.xAutoClaim(cfg.stream, cfg.group, CONSUMER, 60000, '0-0', { COUNT: BATCH });
    const stale = (claimed?.messages ?? []).filter(Boolean);
    if (stale.length) {
      await store(stale);
      await r.xAck(cfg.stream, cfg.group, stale.map((e) => e.id));
      log('info', 'recovered pending entries', { count: stale.length });
    }
  } catch (e) {
    log('warn', 'autoclaim failed', { error: e.message });
  }
}
