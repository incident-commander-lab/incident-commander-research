"""Cluster traffic replayers: open-loop timing and the request mix follow the source profile."""
import asyncio
import json
import os
import sys

import httpx

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "cluster", "load", "replay"))


def test_nasa_profile_keeps_only_counts():
    p = json.load(open(os.path.join(ROOT, "cluster/load/logs/nasa-ksc-1995-07-13.json")))
    assert p["minutes"] == len(p["per_minute"]) == 1440 and p["classes"] == ["static", "listing", "detail", "search", "error"]
    assert all(len(r) == 5 and all(isinstance(x, int) for x in r) for r in p["per_minute"])
    assert sum(map(sum, p["per_minute"])) == 134202          # the whole day, as parsed on 22 Oct
    assert "freely redistributed" in p["source"]


def test_log_replay_sends_the_profile_scaled(tmp_path, monkeypatch):
    import replay
    prof = {"classes": ["static", "listing", "detail", "search", "error"], "per_minute": [[2, 1, 1, 1, 1], [0, 0, 0, 0, 0], [4, 0, 0, 0, 0]]}
    (tmp_path / "p.json").write_text(json.dumps(prof))
    monkeypatch.setattr(replay, "PROFILE", str(tmp_path / "p.json"))
    monkeypatch.setattr(replay, "TIME_SCALE", 600.0)       # 0.1 s per source minute
    monkeypatch.setattr(replay, "SCALE", 2.0)
    monkeypatch.setattr(replay, "MINUTES", 3)
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.path)
        if req.url.path == "/api/Products" and not seen[:-1]:
            return httpx.Response(200, json={"data": [{"id": 7, "image": "a.jpg"}]})
        return httpx.Response(404 if req.url.path.startswith("/ftp/") else 200, json={})

    async def run():
        async with httpx.AsyncClient(base_url="http://shop", transport=httpx.MockTransport(handler)) as c:
            await replay.replay_log(c)
            await asyncio.sleep(0.2)
    asyncio.run(run())
    sent = seen[1:]                                         # first call lists the products
    assert len(sent) == (6 + 0 + 4) * 2
    assert sent.count("/assets/public/images/products/a.jpg") == (2 + 4) * 2
    assert sum(p.startswith("/rest/products/search") for p in sent) == 2
    assert replay.stats["error 4xx"] == 2


def test_attack_payloads_are_mit_and_aimed_at_the_shop():
    import replay
    d = json.load(open(os.path.join(ROOT, "cluster/load/attacks/payloads.json")))
    assert "MIT" in d["source"] and os.path.exists(os.path.join(ROOT, "cluster/load/attacks/LICENSE-PayloadsAllTheThings"))
    assert {k: len(v) for k, v in d["payloads"].items()} == {"sqli": 60, "xss": 60, "traversal": 60, "cmdi": 60}
    reqs = replay._attack_requests(d["payloads"])
    assert len(reqs) == 60 * 2 + 60 * 3
    assert {r[1].split("?")[0].rsplit("/", 1)[0] for r in reqs} <= {"/rest/products", "/rest/user", "/ftp", "/api"}
    assert all(" " not in path for _, path, _ in reqs)          # every payload URL-encoded
