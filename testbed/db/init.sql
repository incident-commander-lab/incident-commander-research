-- Schema for the test bed. Runs once, when the Postgres volume is first created.
CREATE TABLE IF NOT EXISTS edits (
  id          bigserial PRIMARY KEY,
  event_id    text NOT NULL UNIQUE,      -- source's own id: re-delivery is idempotent
  ts          timestamptz NOT NULL,      -- event time (from the source)
  source      text NOT NULL,
  kind        text NOT NULL,
  wiki        text NOT NULL,
  title       text NOT NULL,
  bot         boolean NOT NULL DEFAULT false,
  delta       integer,
  inserted_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS edits_ts_idx ON edits (ts);
CREATE INDEX IF NOT EXISTS edits_wiki_ts_idx ON edits (wiki, ts);
