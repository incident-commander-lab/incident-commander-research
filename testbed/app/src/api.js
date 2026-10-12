// API: read side over Postgres, plus the app's own request metrics (rate, errors, latency)
// that the SLO alerts are written against.
import express from 'express';
import { cfg, log, metrics, pgPool } from './common.js';

const PORT = Number(process.env.PORT || 3000);
const pool = pgPool();
const app = express();

const reqs = new metrics.Counter({ name: 'pulse_http_requests_total', help: 'HTTP requests', labelNames: ['route', 'status'] });
const latency = new metrics.Histogram({ name: 'pulse_http_request_seconds', help: 'HTTP latency', labelNames: ['route'], buckets: [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5] });

app.use((req, res, next) => {
  const end = latency.startTimer();
  res.on('finish', () => {
    const route = req.route?.path ?? 'unmatched';
    reqs.inc({ route, status: String(res.statusCode) });
    end({ route });
  });
  next();
});

const q = (sql, params) => pool.query({ text: sql, values: params, query_timeout: 5000 });

app.get('/healthz', async (_req, res) => {
  try {
    await q('SELECT 1');
    res.send('ok');
  } catch (e) {
    res.status(503).send('db unavailable');
  }
});

// Edits per minute over the last N minutes, per wiki (top 10 wikis).
app.get('/api/rate', async (req, res) => {
  const minutes = Math.min(Number(req.query.minutes || 10), 120);
  try {
    const { rows } = await q(
      `SELECT wiki, date_trunc('minute', ts) AS minute, count(*)::int AS edits
         FROM edits WHERE ts > now() - make_interval(mins => $1)
        GROUP BY wiki, minute ORDER BY minute DESC, edits DESC`, [minutes]);
    res.json(rows);
  } catch (e) {
    log('error', 'query failed', { route: '/api/rate', error: e.message });
    res.status(500).json({ error: 'query failed' });
  }
});

// Most-edited titles in the last N minutes.
app.get('/api/top', async (req, res) => {
  const minutes = Math.min(Number(req.query.minutes || 15), 240);
  try {
    const { rows } = await q(
      `SELECT wiki, title, count(*)::int AS edits, sum(abs(coalesce(delta,0)))::int AS bytes_changed
         FROM edits WHERE ts > now() - make_interval(mins => $1) AND kind IN ('edit','new')
        GROUP BY wiki, title ORDER BY edits DESC LIMIT 25`, [minutes]);
    res.json(rows);
  } catch (e) {
    log('error', 'query failed', { route: '/api/top', error: e.message });
    res.status(500).json({ error: 'query failed' });
  }
});

// Freshness: how far behind real time the stored data is (the end-to-end SLI).
app.get('/api/freshness', async (_req, res) => {
  try {
    const { rows } = await q(`SELECT extract(epoch FROM now() - max(ts))::float AS seconds_behind, count(*)::int AS total FROM edits`);
    res.json(rows[0]);
  } catch (e) {
    log('error', 'query failed', { route: '/api/freshness', error: e.message });
    res.status(500).json({ error: 'query failed' });
  }
});

app.get('/metrics', async (_req, res) => {
  res.set('Content-Type', metrics.register.contentType);
  res.end(await metrics.register.metrics());
});

app.listen(PORT, () => log('info', 'api listening', { port: PORT, service: cfg.service }));
