// Collector: reads a real public stream (Wikimedia EventStreams) and appends each event
// to a Redis stream. It also records the raw events to hourly files, so the exact same
// traffic can be replayed later for every agent under test (REPLAY_FILE).
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { cfg, log, metrics, redis, release, serveMetrics, sleep } from './common.js';
import { createSSEParser, normalizeWikimedia } from './sources.js';
import { egressProbe, runReplay, urlOk } from './replay.js';

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
  // A connection can stall silently (open, but no bytes) on some networks. The stream carries tens of
  // events per second, so STALL_MS of silence means a stall: abort and resume from Last-Event-ID.
  const STALL_MS = Number(process.env.STALL_MS || 15000);
  for (;;) {
    const ac = new AbortController();
    let lastChunk = Date.now();
    const stallTimer = setInterval(() => { if (Date.now() - lastChunk > STALL_MS) ac.abort(new Error('stalled')); }, 1000);
    try {
      const headers = { 'User-Agent': UA, Accept: 'text/event-stream' };
      if (lastId) headers['Last-Event-ID'] = lastId;   // resume where we dropped
      const res = await fetch(URL_, { headers, signal: ac.signal });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      isConnected = true; connected.set({ source: 'wikipedia' }, 1); backoff = 1000;
      log('info', 'connected', { source: 'wikipedia', resume: Boolean(lastId), release: release.version });
      const feed = createSSEParser();
      const dec = new TextDecoder();
      for await (const chunk of res.body) {
        lastChunk = Date.now();
        for (const ev of feed(dec.decode(chunk, { stream: true }))) {
          if (ev.id) lastId = ev.id;
          record(ev.data);
          await enqueue(ev.data);
        }
      }
      throw new Error('stream ended');
    } catch (e) {
      clearInterval(stallTimer);
      if (ac.signal.aborted) { log('warn', 'source stalled, reconnecting', { source: 'wikipedia', stall_ms: STALL_MS }); backoff = 1000; }
      isConnected = false; connected.set({ source: 'wikipedia' }, 0); reconnects.inc({ source: 'wikipedia' });
      log('warn', 'source disconnected', { source: 'wikipedia', error: e.message, retry_ms: backoff });
      await sleep(backoff);
      backoff = Math.min(backoff * 2, 60000);
    }
  }
}

// ---------------------------------------------------------------- replay mode
// See replay.js: anchored to SESSION_START (epoch seconds, shared with the fault injector), and
// unavailable whenever the live source would be (wrong URL, egress relay down).
async function replay() {
  const startMs = process.env.SESSION_START ? Number(process.env.SESSION_START) * 1000 : Date.now();
  const urlGood = urlOk(URL_);
  const probe = egressProbe({
    host: process.env.EGRESS_PROBE_HOST || 'stream.wikimedia.org',   // the egress relay (extra_hosts)
    port: Number(process.env.EGRESS_PROBE_PORT || 8443),
    onChange: (ok) => log(ok ? 'info' : 'warn', ok ? 'egress reachable' : 'egress unreachable'),
  });
  const available = () => urlGood && probe.up();
  let shown = null;
  const gauge = () => {
    const ok = available();
    isConnected = ok;
    connected.set({ source: 'wikipedia' }, ok ? 1 : 0);
    if (ok !== shown) {
      if (!ok) reconnects.inc({ source: 'wikipedia' });
      shown = ok;
    }
  };
  gauge();
  const gaugeTimer = setInterval(gauge, 1000);
  if (!urlGood) log('warn', 'source disconnected', { source: 'wikipedia', error: `HTTP 404 ${URL_}` });
  log('info', 'replaying', { file: REPLAY_FILE, speed: REPLAY_SPEED, session_start: new Date(startMs).toISOString() });

  async function* records() {
    const rl = readline.createInterface({ input: fs.createReadStream(REPLAY_FILE) });
    for await (const line of rl) {
      if (line.trim()) yield JSON.parse(line);
    }
  }
  const stats = await runReplay(records(), {
    startMs, speed: REPLAY_SPEED, now: Date.now, sleep, available,
    emit: enqueue,
    drop: () => skipped.inc({ source: 'wikipedia', reason: 'source_unavailable' }),
  });
  clearInterval(gaugeTimer);
  probe.stop();
  log('info', 'replay finished', { file: REPLAY_FILE, ...stats });
  isConnected = false;
  connected.set({ source: 'wikipedia' }, 0);
}

await (REPLAY_FILE ? replay() : live());
