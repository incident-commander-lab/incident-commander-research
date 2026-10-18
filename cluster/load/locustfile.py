"""Shopper journeys against the shop, through the ingress (what users see).

With TRACE_FILE set, the number of active shoppers follows a real public trace (hourly page views
of German Wikipedia: one time zone, so a regional shop's daily cycle), compressed TIME_SCALE times
(default 24: one real day per hour) and scaled so the busiest hour has PEAK_USERS shoppers.
Without TRACE_FILE it is a constant-load run (-u/-t), used for the CPU-surge fault. Mostly anonymous browsing, so it spreads
across Juice Shop replicas (each replica has its own SQLite; logged-in journeys come later and
stay on one replica via the ingress's sticky cookie).
"""
import json
import os
import random

from locust import HttpUser, LoadTestShape, between, task

SEARCH_TERMS = ["apple", "juice", "banana", "lemon", "melon", "card", "shirt", "sticker", "mug", "raspberry"]
QUESTIONS = ["Is this good for kids?", "How long does it keep?", "Is it sweet?", "Is it worth the price?",
             "What does it go well with?"]


class Shopper(HttpUser):
    wait_time = between(1, 4)

    def on_start(self):
        r = self.client.get("/api/Products", name="/api/Products")
        ids = [p["id"] for p in r.json().get("data", [])] if r.ok else []
        self.ids = ids or list(range(1, 30))

    @task(6)
    def browse(self):
        self.client.get("/rest/products/search?q=", name="/rest/products/search (all)")

    @task(5)
    def search(self):
        self.client.get(f"/rest/products/search?q={random.choice(SEARCH_TERMS)}", name="/rest/products/search?q=")

    @task(4)
    def product(self):
        pid = random.choice(self.ids)
        self.client.get(f"/api/Products/{pid}", name="/api/Products/[id]")
        self.client.get(f"/rest/products/{pid}/reviews", name="/rest/products/[id]/reviews")

    @task(1)
    def ask_assistant(self):   # ~6% of actions: the GPU's workload
        self.client.post("/assistant/ask", name="/assistant/ask", timeout=180,
                         json={"product_id": random.choice(self.ids), "question": random.choice(QUESTIONS)})


if os.environ.get("TRACE_FILE"):
    class TraceShape(LoadTestShape):
        """Users over time from the trace: each trace hour lasts 3600/TIME_SCALE seconds.
        START_HOUR picks where in the week to begin; HOURS how many trace hours to play."""
        trace = json.load(open(os.environ["TRACE_FILE"]))["views"]
        peak = int(os.environ.get("PEAK_USERS", "60"))
        scale = float(os.environ.get("TIME_SCALE", "24"))
        start = int(os.environ.get("START_HOUR", "0"))
        hours = int(os.environ.get("HOURS", "24"))
        top = max(trace)

        def tick(self):
            t = self.get_run_time()
            h = t * self.scale / 3600                     # trace hours elapsed
            if h >= self.hours:
                return None
            i = self.start + int(h)
            a, b = self.trace[i % len(self.trace)], self.trace[(i + 1) % len(self.trace)]
            frac = (a + (b - a) * (h - int(h))) / self.top  # linear between hours: no steps
            users = max(1, round(self.peak * frac))
            return users, max(1.0, users / 10)
