"""In-memory K8s state for the demo. Pure Python, no external deps."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Pod:
    name: str
    namespace: str
    cpu_usage: float
    phase: str = "Running"


@dataclass
class Deployment:
    name: str
    namespace: str
    replicas: int


@dataclass
class FakeCluster:
    pods: dict[str, Pod] = field(default_factory=dict)
    deployments: dict[str, Deployment] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "FakeCluster":
        c = cls()
        c.deployments["payments-api"] = Deployment("payments-api", "payments", 3)
        c.pods["payments-api-0"] = Pod("payments-api-0", "payments", cpu_usage=0.92)
        c.pods["payments-api-1"] = Pod("payments-api-1", "payments", cpu_usage=0.55)
        c.pods["payments-api-2"] = Pod("payments-api-2", "payments", cpu_usage=0.61)
        return c

    def scale(self, deployment: str, delta: int) -> int:
        d = self.deployments[deployment]
        d.replicas = max(0, d.replicas + delta)
        return d.replicas

    def get(self, resource: str, namespace: str = "default") -> list[dict]:
        if resource == "pods":
            return [
                {"name": p.name, "namespace": p.namespace, "phase": p.phase,
                 "cpu_usage": p.cpu_usage}
                for p in self.pods.values() if p.namespace == namespace
            ]
        if resource in {"deployments", "deploy"}:
            return [
                {"name": d.name, "namespace": d.namespace, "replicas": d.replicas}
                for d in self.deployments.values() if d.namespace == namespace
            ]
        return []


CLUSTER = FakeCluster.default()
