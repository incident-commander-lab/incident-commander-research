// Shared plumbing: config, Redis, Postgres, metrics server, logging.
import http from 'node:http';
import pg from 'pg';
import { createClient } from 'redis';
import client from 'prom-client';

export const cfg = {
  redisUrl: process.env.REDIS_URL || 'redis://localhost:6379',
  pgUrl: process.env.DATABASE_URL || 'postgres://pulse:pulse@localhost:5432/pulse',
  stream: process.env.STREAM_KEY || 'events',
  group: process.env.STREAM_GROUP || 'workers',
  // Cap the Redis stream so a stalled worker fills memory slowly, not instantly
  streamMaxLen: Number(process.env.STREAM_MAXLEN || 200000),
  metricsPort: Number(process.env.METRICS_PORT || 9100),
  service: process.env.SERVICE_NAME || 'pulse',
};

client.collectDefaultMetrics({ labels: { service: cfg.service } });
export const metrics = client;

// One JSON line per log event: easy for Loki or `kubectl logs | jq`, and for the agent's log checks.
export function log(level, msg, fields = {}) {
  process.stdout.write(JSON.stringify({ ts: new Date().toISOString(), level, service: cfg.service, msg, ...fields }) + '\n');
}

export async function redis() {
  const r = createClient({ url: cfg.redisUrl });
  r.on('error', (e) => log('error', 'redis error', { error: e.message }));
  await r.connect();
  return r;
}

export function pgPool() {
  const pool = new pg.Pool({ connectionString: cfg.pgUrl, max: Number(process.env.PG_POOL || 5) });
  pool.on('error', (e) => log('error', 'postgres pool error', { error: e.message }));
  return pool;
}

// Collector and worker have no HTTP API of their own: expose /metrics and /healthz here.
export function serveMetrics(port = cfg.metricsPort, healthy = () => true) {
  http.createServer(async (req, res) => {
    if (req.url === '/metrics') {
      res.setHeader('Content-Type', client.register.contentType);
      res.end(await client.register.metrics());
    } else if (req.url === '/healthz') {
      const ok = healthy();
      res.statusCode = ok ? 200 : 503;
      res.end(ok ? 'ok' : 'unhealthy');
    } else {
      res.statusCode = 404;
      res.end();
    }
  }).listen(port, () => log('info', 'metrics listening', { port }));
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
