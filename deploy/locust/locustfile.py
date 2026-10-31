"""Load test for the read-heavy paths.

    pip install locust
    RAGSCOPE_API_KEY=rk_... KB_ID=<uuid> HOST=http://localhost:8080 \
        locust -f deploy/locust/locustfile.py --headless -u 20 -r 2 -t 3m

Why these endpoints and not `/chat`: a load test of the LLM path measures the
upstream provider, not this service, and burns real tokens. The mix below targets
what *this* code controls — retrieval, auth lookup, metrics scraping — plus a small
share of embedding-only search traffic.

Note on the rate limiter: `/public/*` allows 60 requests/minute per key by default,
so a serious run needs either several keys or a raised limit. The default profile
is deliberately below that ceiling so the numbers mean "service capacity", not
"429 capacity".
"""

import os
import random

from locust import HttpUser, between, task

API_KEY = os.environ.get("RAGSCOPE_API_KEY", "")
KB_ID = os.environ.get("KB_ID", "")

QUERIES = [
    "X1 的电池容量是多少",
    "进水还能保修吗",
    "X1 和 X2 的屏幕有什么区别",
    "保修期多久",
    "支持哪些充电协议",
    "如何申请退货",
]


class ReadUser(HttpUser):
    wait_time = between(0.5, 2.0)
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    @task(1)
    def health(self) -> None:
        self.client.get("/healthz", name="/healthz")

    @task(2)
    def metrics(self) -> None:
        # Prometheus scrapes this every 15s per replica; it must stay cheap.
        self.client.get("/metrics", name="/metrics")

    @task(4)
    def list_kbs(self) -> None:
        self.client.get("/api/v1/public/kbs", headers=self.headers, name="/public/kbs")

    @task(6)
    def search(self) -> None:
        if not KB_ID:
            return
        self.client.post(
            f"/api/v1/public/kbs/{KB_ID}/search",
            json={"query": random.choice(QUERIES), "top_k": 5},
            headers=self.headers,
            name="/public/kbs/{id}/search",
        )

    @task(2)
    def mcp_tools(self) -> None:
        self.client.get("/api/v1/mcp/tools", headers=self.headers, name="/mcp/tools")
