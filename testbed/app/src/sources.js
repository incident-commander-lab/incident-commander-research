// Pure functions for the event sources: SSE parsing and normalisation. Unit-tested.

// Incremental Server-Sent Events parser. Feed it text chunks; it returns complete
// events ({ id, event, data }) and keeps any partial event for the next chunk.
export function createSSEParser() {
  let buf = '';
  return function feed(chunk) {
    buf += chunk.replace(/\r\n/g, '\n');
    const out = [];
    let sep;
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const ev = { id: null, event: 'message', data: '' };
      const data = [];
      for (const line of block.split('\n')) {
        if (!line || line.startsWith(':')) continue;          // comment / keep-alive
        const i = line.indexOf(':');
        const field = i === -1 ? line : line.slice(0, i);
        const value = i === -1 ? '' : line.slice(i + 1).replace(/^ /, '');
        if (field === 'data') data.push(value);
        else if (field === 'id') ev.id = value;
        else if (field === 'event') ev.event = value;
      }
      if (data.length) {
        ev.data = data.join('\n');
        out.push(ev);
      }
    }
    return out;
  };
}

// Wikimedia EventStreams `recentchange` event -> the flat record we store.
// Returns null for events we deliberately skip (canary heartbeats).
export function normalizeWikimedia(raw) {
  const e = typeof raw === 'string' ? JSON.parse(raw) : raw;
  if (e?.meta?.domain === 'canary') return null;
  const lenNew = e.length?.new ?? null;
  const lenOld = e.length?.old ?? null;
  return {
    source: 'wikipedia',
    event_id: String(e.meta?.id ?? `${e.wiki}:${e.id ?? e.meta?.dt}`),
    ts: e.meta?.dt ?? new Date((e.timestamp ?? 0) * 1000).toISOString(),
    kind: e.type ?? 'unknown',
    wiki: e.wiki ?? e.server_name ?? 'unknown',
    title: (e.title ?? '').slice(0, 500),
    bot: Boolean(e.bot),
    delta: lenNew != null && lenOld != null ? lenNew - lenOld : null,
  };
}
