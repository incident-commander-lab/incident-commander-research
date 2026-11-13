"""Traffic replayers (open loop: requests are sent on the source's schedule, whether or not the shop
keeps up, unlike Locust's users who wait for each answer).

    MODE=log     replays a public web-server log's timing and request mix (cluster/load/logs/*.json:
                 requests per minute by class, no hosts) against Juice Shop's own endpoints
    MODE=attack  labelled-malicious bursts: payloads from PayloadsAllTheThings (MIT) sent at the times
                 in ATTACK_SCHEDULE (the chaos injector writes the same times into the sealed key)

Classes map to Juice Shop: static -> product images, listing -> /api/Products, detail -> a product or
its reviews, search -> /rest/products/search, error -> a missing file (404).
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections import Counter

import httpx

HOST = os.environ.get("HOST", "http://traefik.kube-system.svc.cluster.local")
MODE = os.environ.get("MODE", "log")
PROFILE = os.environ.get("PROFILE", "/logs/nasa-ksc-1995-07-13.json")
TIME_SCALE = float(os.environ.get("TIME_SCALE", "24"))     # 24: one source day per hour, like the Locust trace
SCALE = float(os.environ.get("SCALE", "4"))                # request multiplier (NASA peak 405/min -> ~27 req/s)
START_MIN = int(os.environ.get("START_MINUTE", "0"))       # where in the source day to begin
MINUTES = int(os.environ.get("MINUTES", "1440"))           # how many source minutes to play (wraps around)
MAX_INFLIGHT = int(os.environ.get("MAX_INFLIGHT", "64"))
PAYLOADS = os.environ.get("PAYLOADS", "/attacks/payloads.json")
ATTACK_S = float(os.environ.get("ATTACK_SECONDS", "480"))   # one burst; the injector starts and times it
ATTACK_RATE = float(os.environ.get("ATTACK_RATE", "8"))      # requests per second from the one attacking client
WORDS = ["apple", "juice", "lemon", "banana", "melon", "orange", "carrot", "fruit", "green", "eggfruit"]

stats: Counter = Counter()


async def _get(c: httpx.AsyncClient, sem: asyncio.Semaphore, path: str, label: str):
    async with sem:
        try:
            r = await c.get(path)
            stats[f"{label} {r.status_code // 100}xx"] += 1
        except httpx.HTTPError as e:
            stats[f"{label} {type(e).__name__}"] += 1


def _paths(products: list[dict]) -> dict:
    ids = [p["id"] for p in products] or [1]
    images = [f"/assets/public/images/products/{p['image']}" for p in products if p.get("image")] or ["/"]
    return {
        "static": lambda: random.choice(images),
        "listing": lambda: "/api/Products",
        "detail": lambda: random.choice([f"/api/Products/{random.choice(ids)}", f"/rest/products/{random.choice(ids)}/reviews"]),
        "search": lambda: f"/rest/products/search?q={random.choice(WORDS)}",
        "error": lambda: f"/ftp/missing-{random.randint(1, 999)}.md",
    }


async def replay_log(c: httpx.AsyncClient):
    prof = json.load(open(PROFILE))
    classes, rows = prof["classes"], prof["per_minute"]
    products = (await c.get("/api/Products")).json().get("data", [])
    pick = _paths(products)
    sem = asyncio.Semaphore(MAX_INFLIGHT)
    slot = 60.0 / TIME_SCALE              # one source minute lasts this many seconds
    t0 = time.monotonic()
    for n in range(MINUTES):
        row = rows[(START_MIN + n) % len(rows)]
        sends = [(random.uniform(0, slot), cls) for cls, k in zip(classes, row)
                 for _ in range(int(k * SCALE) + (random.random() < (k * SCALE) % 1))]
        for at, cls in sorted(sends):
            await asyncio.sleep(max(0.0, t0 + n * slot + at - time.monotonic()))
            asyncio.create_task(_get(c, sem, pick[cls](), cls))
        if n % int(60 / slot or 1) == 0:   # about once a minute of wall time
            print(f"[{time.strftime('%H:%M:%S')}] source minute {(START_MIN + n) % len(rows)}: {dict(stats)}", flush=True)
        await asyncio.sleep(max(0.0, t0 + (n + 1) * slot - time.monotonic()))


def _attack_requests(payloads: dict) -> list[tuple[str, str, dict | None]]:
    """(method, path, json body) per payload, aimed at the Juice Shop endpoints each attack targets."""
    from urllib.parse import quote
    out = []
    for p in payloads.get("sqli", []):
        out += [("GET", f"/rest/products/search?q={quote(p)}", None),
                ("POST", "/rest/user/login", {"email": p, "password": "x"})]
    out += [("GET", f"/rest/products/search?q={quote(p)}", None) for p in payloads.get("xss", [])]
    out += [("GET", f"/ftp/{quote(p, safe='')}", None) for p in payloads.get("traversal", [])]
    out += [("GET", f"/api/Products?name={quote(p)}", None) for p in payloads.get("cmdi", [])]
    return out


async def replay_attack(c: httpx.AsyncClient):
    reqs = _attack_requests(json.load(open(PAYLOADS))["payloads"])
    sem = asyncio.Semaphore(MAX_INFLIGHT)
    t0, n = time.monotonic(), 0

    async def send(method, path, body):
        async with sem:
            try:
                r = await (c.post(path, json=body) if method == "POST" else c.get(path))
                stats[f"attack {r.status_code // 100}xx"] += 1
            except httpx.HTTPError as e:
                stats[f"attack {type(e).__name__}"] += 1
    while time.monotonic() - t0 < ATTACK_S:
        asyncio.create_task(send(*random.choice(reqs)))
        n += 1
        if n % int(ATTACK_RATE * 60) == 0:
            print(f"[{time.strftime('%H:%M:%S')}] attack: {dict(stats)}", flush=True)
        await asyncio.sleep(random.expovariate(ATTACK_RATE))


async def main():
    async with httpx.AsyncClient(base_url=HOST, timeout=30) as c:
        if MODE == "log":
            await replay_log(c)
        elif MODE == "attack":
            c.headers["User-Agent"] = "Mozilla/5.0 (compatible; scan/1.0)"
            await replay_attack(c)
        else:
            raise SystemExit(f"unknown MODE {MODE}")
    await asyncio.sleep(5)
    print("done:", dict(stats), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
