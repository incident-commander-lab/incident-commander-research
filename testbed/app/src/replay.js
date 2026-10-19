// Replay of recorded traffic for scored sessions (docs/paper/RQ3_PROTOCOL.md).
//
// Two properties make two agents see the same traffic:
//  1. Wall-clock anchoring. The i-th recorded event is due at SESSION_START + (t_i - t_0).
//     A collector that (re)starts mid-session seeks to "now" instead of starting the file over,
//     so restarts, rollbacks and hung processes never shift the traffic that follows.
//  2. The replay source fails the way the live source would. Events are dropped (not enqueued)
//     while the configured stream URL is wrong (bad_config_collector) or the egress relay is
//     down (upstream_cut), so those faults have their real symptoms under replay too.
import net from 'node:net';

// The path the live collector must use; anything else is a 404 from Wikimedia.
export const CANONICAL_PATH = '/v2/stream/recentchange';

export function urlOk(url) {
  try {
    return new URL(url).pathname === CANONICAL_PATH;
  } catch {
    return false;
  }
}

// Drives a replay over parsed records ({ recv_ts, data }), in file order.
//  - startMs: wall-clock time the first recorded event maps to
//  - now(), sleep(ms): injectable clock (tests)
//  - available(): is the source reachable right now (URL and egress)
//  - emit(data): enqueue one event; drop(data, reason): an event the source did not deliver
// Events already in the past when the replay starts are skipped (the seek); after the first
// event is delivered or dropped on time, nothing is skipped for lateness.
export async function runReplay(records, { startMs, speed = 1, now, sleep, available, emit, drop }) {
  let t0 = null, seeking = true;
  const stats = { emitted: 0, dropped: 0, skipped: 0 };
  for await (const rec of records) {
    const t = Date.parse(rec.recv_ts);
    if (Number.isNaN(t)) continue;
    if (t0 == null) t0 = t;
    const due = startMs + (t - t0) / speed;
    if (seeking && due < now()) {
      stats.skipped++;
      continue;
    }
    seeking = false;
    const wait = due - now();
    if (wait > 0) await sleep(wait);
    if (available()) {
      await emit(rec.data);
      stats.emitted++;
    } else {
      drop(rec.data, 'source_unavailable');
      stats.dropped++;
    }
  }
  return stats;
}

// Polls a TCP port on the egress relay. The relay answers on its own health port, so probing
// never opens a connection to Wikimedia. Returns { up(): boolean, stop() }.
export function egressProbe({ host, port, intervalMs = 2000, timeoutMs = 1000, onChange = () => {} }) {
  let state = true, timer = null;
  const check = () => {
    const sock = net.connect({ host, port });
    let settled = false;
    const done = (ok) => {
      if (settled) return;
      settled = true;
      sock.destroy();
      if (ok !== state) { state = ok; onChange(ok); }
    };
    sock.setTimeout(timeoutMs, () => done(false));
    sock.once('connect', () => done(true));
    sock.on('error', () => done(false));   // stays attached: a late reset must not crash the collector
  };
  check();
  timer = setInterval(check, intervalMs);
  timer.unref?.();
  return { up: () => state, stop: () => clearInterval(timer) };
}
