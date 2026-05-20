"""Tiny FastAPI on :8081 mimicking VictoriaMetrics /api/v1/query."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI, Query

app = FastAPI(title="mock-vm")


@app.get("/api/v1/query")
def query(query: str = Query(...)):
    # Deterministic canned series — 0.9 for payments-api, 0.4 otherwise.
    if "payments" in query.lower() or "cpu_usage" in query.lower():
        return {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {"metric": {"pod": "payments-api-0"}, "value": [0, "0.92"]},
                    {"metric": {"pod": "payments-api-1"}, "value": [0, "0.55"]},
                ],
            },
        }
    return {"status": "success", "data": {"resultType": "vector", "result": []}}


@app.get("/readyz")
def readyz():
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8081, log_level="warning")
