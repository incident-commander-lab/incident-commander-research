// Collector: reads a real public stream (Wikimedia EventStreams) and appends each event
// to a Redis stream. It also records the raw events to hourly files, so the exact same
// traffic can be replayed later for every agent under test (REPLAY_FILE).
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { cfg, log, metrics, redis, release, serveMetrics, sleep } from './common.js';
import { createSSEParser, normalizeWikimedia } from './sources.js';

const URL_ = process.env.WIKIMEDIA_URL || 'https://stream.wikimedia.org/v2/stream/recentchange';
// Wikimedia asks every client to identify itself with a contact (User-Agent policy).
const UA = process.env.USER_AGENT || 'pulse-testbed/0.1 (research test bed; set USER_AGENT with a contact)';
const RECORD_DIR = process.env.RECORD_DIR || '';
const REPLAY_FILE = process.env.REPLAY_FILE || '';
const REPLAY_SPEED = Number(process.env.REPLAY_SPEED || 1);

const received = new metrics.Counter({ name: 'pulse_events_received_total', help: 'Events received from a source', labelNames: ['source', 'kind'] });
const enqueued = new metrics.Counter({ name: 'pulse_events_enqueued_total', help: 'Events written to the Redis stream', labelNames: ['source'] });
const skipped = new metrics.Counter({ name: 'pulse_events_skipped_total', help: 'Events skipped (canary/unparseable)', labelNames: ['source', 'reason'] });
const connected = new metrics.Gauge({ name: 'pulse_source_connected', help: '1 while the source stream is connected', labelNames: ['source'] });
const reconnects = new metrics.Counter({ name: 'pulse_source_reconnects_total', help: 'Reconnects to the source', labelNames: ['source'] });
const lastEvent = new metrics.Gauge({ name: 'pulse_source_last_event_timestamp_seconds', help: 'Unix time of the last event received', labelNames: ['source'] });
const enqueueErrors = new metrics.Counter({ name: 'pulse_enqueue_errors_total', help: 'Failed writes to Redis', labelNames: ['source'] });

let isConnected = false;
serveMetrics(cfg.metricsPort, () => isConnected || Boolean(REPLAY_FILE));

const r = await redis();

// ---------------------------------------------------------------- recorder
let recFile = null, recHour = null;
function record(rawData) {
  if (!RECORD_DIR) return;
  const now = new Date();
  const hour = now.toISOString().slice(0, 13).replace(/[-:T]/g, '');
  if (hour !== recHour) {
    recFile?.end();
    fs.mkdirSync(RECORD_DIR, { recursive: true });
    recFile = fs.createWriteStream(path.join(RECORD_DIR, `wikipedia-${hour}.ndjson`), { flags: 'a' });
    recHour = hour;
  }
  recFile.write(JSON.stringify({ recv_ts: now.toISOString(), data: rawData }) + '\n');
}

async function enqueue(rawData) {
  let ev;
  try {
    ev = normalizeWikimedia(rawData);
  } catch {
    skipped.inc({ source: 'wikipedia', reason: 'unparseable' });
    return;
  }
  if (!ev) {
    skipped.inc({ source: 'wikipedia', reason: 'canary' });
    return;
  }
  received.inc({ source: ev.source, kind: ev.kind });
  lastEvent.set({ source: ev.source }, Date.now() / 1000);
  try {
    await r.xAdd(cfg.stream, '*', Object.fromEntries(Object.entries(ev).map(([k, v]) => [k, v == null ? '' : String(v)])),
      { TRIM: { strategy: 'MAXLEN', strategyModifier: '~', threshold: cfg.streamMaxLen } });
    enqueued.inc({ source: ev.source });
  } catch (e) {
    enqueueErrors.inc({ source: ev.source });
    log('error', 'enqueue failed', { error: e.message });
  }
}

// ---------------------------------------------------------------- live mode
async function live() {
  let lastId = null, backoff = 1000;
  for (;;) {
    try {
      const headers = { 'User-Agent': UA, Accept: 'text/event-stream' };
      if (lastId) headers['Last-Event-ID'] = lastId;   // resume where we dropped
      const res = await fetch(URL_, { headers });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      isConnected = true; connected.set({ source: 'wikipedia' }, 1); backoff = 1000;
      log('info', 'connected', { source: 'wikipedia', resume: Boolean(lastId), release: release.version });
      const feed = createSSEParser();
      const dec = new TextDecoder();
      for await (const chunk of res.body) {
        for (const ev of feed(dec.decode(chunk, { stream: true }))) {
          if (ev.id) lastId = ev.id;
          record(ev.data);
          await enqueue(ev.data);
        }
      }
      throw new Error('stream ended');
    } catch (e) {
      isConnected = false; connected.set({ source: 'wikipedia' }, 0); reconnects.inc({ source: 'wikipedia' });
      log('warn', 'source disconnected', { source: 'wikipedia', error: e.message, retry_ms: backoff });
      await sleep(backoff);
      backoff = Math.min(backoff * 2, 60000);
    }
  }
}

// ---------------------------------------------------------------- replay mode
async function replay() {
  log('info', 'replaying', { file: REPLAY_FILE, speed: REPLAY_SPEED });
  connected.set({ source: 'wikipedia' }, 1);
  const rl = readline.createInterface({ input: fs.createReadStream(REPLAY_FILE) });
  let t0 = null, w0 = null;
  for await (const line of rl) {
    if (!line.trim()) continue;
    const { recv_ts, data } = JSON.parse(line);
    const t = Date.parse(recv_ts);
    if (t0 == null) { t0 = t; w0 = Date.now(); }
    const due = w0 + (t - t0) / REPLAY_SPEED;     // keep the original timing (bursts included)
    if (due > Date.now()) await sleep(due - Date.now());
    await enqueue(data);
  }
  log('info', 'replay finished', { file: REPLAY_FILE });
  connected.set({ source: 'wikipedia' }, 0);
}

await (REPLAY_FILE ? replay() : live());
