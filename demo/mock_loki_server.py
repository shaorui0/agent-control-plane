"""Tiny FastAPI on :8082 mimicking Loki /loki/api/v1/query_range."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI, Query

app = FastAPI(title="mock-loki")


@app.get("/loki/api/v1/query_range")
def query_range(query: str = Query(...)):
    if "payments" in query.lower():
        lines = [
            [str(i), f"payments-api log line {i}: cpu 0.9, latency 1200ms"]
            for i in range(5)
        ]
    else:
        lines = []
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [{"stream": {"app": "payments"}, "values": lines}],
        },
    }


@app.get("/readyz")
def readyz():
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8082, log_level="warning")
