"""Shopper journeys against the shop, through the ingress (what users see).

Day 1: the journeys and their mix. Day 2 adds the LoadTestShape that follows a real public
trace (Wikipedia hourly page views, 1 day -> 1 hour). Mostly anonymous browsing, so it spreads
across Juice Shop replicas (each replica has its own SQLite; logged-in journeys come later and
stay on one replica via the ingress's sticky cookie).
"""
import random

from locust import HttpUser, between, task

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
