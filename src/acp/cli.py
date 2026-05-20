"""ACP command-line interface.

`acp <subcommand>` — operator entry point. Each subcommand opens its own short-
lived DB connection so the CLI works against a running server's SQLite file
(WAL mode = safe concurrent reads).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import webbrowser
from pathlib import Path

import typer

from acp import db as _db
from acp.autonomy.controller import AutonomyController
from acp.clock import RealClock
from acp.events.store import WideEventStore
from acp.events.verifier import verify_all
from acp.human.approval import ApprovalQueue
from acp.human.audit import AuditQueue
from acp.registry.loader import load_dir
from acp.registry.store import RegistryStore
from acp.schemas.agent import AutonomyTier
from acp.settings import get_settings

app = typer.Typer(help="Agent Control Plane CLI")
audit_app = typer.Typer(help="Human audit queue")
autonomy_app = typer.Typer(help="Autonomy tier control")
app.add_typer(audit_app, name="audit")
app.add_typer(autonomy_app, name="autonomy")


def _open_conn(db: Path | None = None) -> sqlite3.Connection:
    settings = get_settings()
    path = db or settings.db_path
    conn = _db.connect(path)
    _db.migrate(conn)
    return conn


def _open_registry(conn: sqlite3.Connection, registry_dir: Path | None = None) -> RegistryStore:
    settings = get_settings()
    d = registry_dir or settings.registry_dir
    store = RegistryStore(conn, Path(d))
    if Path(d).exists():
        store.load()
    return store


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(None, help="defaults to ACP_PORT or 8080"),
) -> None:
    """Run the ACP API server."""
    import uvicorn

    settings = get_settings()
    port = port or settings.port
    uvicorn.run("acp.server:app", host=host, port=port, log_level="info")


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


@app.command()
def register(
    file: Path = typer.Option(..., "--file", "-f", help="YAML agent spec to validate + add."),
) -> None:
    """Validate a YAML spec and copy it into the configured registry dir."""
    settings = get_settings()
    if not file.exists():
        typer.echo(f"file not found: {file}", err=True)
        raise typer.Exit(code=2)

    # Validate by loading via load_dir from a tmp dir copy.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / file.name
        shutil.copy(file, tmp_path)
        try:
            agents = load_dir(Path(tmp))
        except Exception as e:
            typer.echo(f"validation failed: {e}", err=True)
            raise typer.Exit(code=1) from e

    if not agents:
        typer.echo("no agents found in file", err=True)
        raise typer.Exit(code=1)

    target_dir = Path(settings.registry_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / file.name
    shutil.copy(file, target)
    typer.echo(f"registered {len(agents)} agent(s): {sorted(agents.keys())} -> {target}")


# ---------------------------------------------------------------------------
# slo
# ---------------------------------------------------------------------------


@app.command()
def slo(
    agent: str = typer.Option(..., "--agent", "-a"),
    task_class: str = typer.Option(..., "--task-class", "-t"),
    window: str = typer.Option("7d", "--window", "-w"),
) -> None:
    """Pretty-print the latest BudgetSnapshot for an (agent, task_class, window)."""
    conn = _open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM slo_snapshots WHERE agent_id = ? AND task_class = ? "
            "AND window_label = ? ORDER BY ts DESC LIMIT 1",
            (agent, task_class, window),
        ).fetchone()
        if row is None:
            typer.echo(f"no snapshot for agent={agent} task_class={task_class} window={window}")
            raise typer.Exit(code=1)
        out = {k: row[k] for k in row.keys()}
        typer.echo(json.dumps(out, indent=2, sort_keys=True, default=str))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# burn
# ---------------------------------------------------------------------------


@app.command()
def burn(agent: str = typer.Option(..., "--agent", "-a")) -> None:
    """Show current burn rates per task_class for an agent."""
    conn = _open_conn()
    try:
        rows = conn.execute(
            "SELECT s.* FROM slo_snapshots s "
            "JOIN (SELECT task_class, window_label, MAX(ts) AS ts FROM slo_snapshots "
            "      WHERE agent_id = ? GROUP BY task_class, window_label) latest "
            "ON s.task_class = latest.task_class AND s.window_label = latest.window_label "
            "AND s.ts = latest.ts WHERE s.agent_id = ?",
            (agent, agent),
        ).fetchall()
        if not rows:
            typer.echo(f"no snapshots for agent={agent}")
            raise typer.Exit(code=1)
        for r in rows:
            typer.echo(
                f"{r['task_class']}/{r['window_label']} burn={r['burn_rate']:.2f} "
                f"remaining={r['budget_remaining']:.2f} class={r['budget_class']}"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


@app.command()
def approve(
    approval_id: str = typer.Argument(...),
    reject: bool = typer.Option(False, "--reject", help="Reject instead of approve."),
    reviewer: str = typer.Option(..., "--reviewer", "-r"),
    notes: str = typer.Option("", "--notes", "-n"),
) -> None:
    """Decide a pending approval."""
    conn = _open_conn()
    try:
        events = WideEventStore(conn, clock=RealClock())
        queue = ApprovalQueue(conn, event_store=events)
        decision = "rejected" if reject else "approved"
        try:
            updated = queue.decide(approval_id, decision, reviewer, notes)  # type: ignore[arg-type]
        except KeyError as e:
            typer.echo(f"not found: {e}", err=True)
            raise typer.Exit(code=2) from e
        except ValueError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"{updated.approval_id} -> {updated.status} by {updated.decided_by}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# audit list / audit decide
# ---------------------------------------------------------------------------


@audit_app.command("list")
def audit_list() -> None:
    """List pending audits."""
    conn = _open_conn()
    try:
        queue = AuditQueue(conn)
        items = queue.list_pending()
        if not items:
            typer.echo("(no pending audits)")
            return
        for f in items:
            typer.echo(f"{f.audit_id}\t{f.reason}\tevent={f.event_id}")
    finally:
        conn.close()


@audit_app.command("decide")
def audit_decide(
    audit_id: str = typer.Argument(...),
    human_label: str = typer.Option(..., "--human-label"),
    reviewer: str = typer.Option("cli@local", "--reviewer", "-r"),
    notes: str = typer.Option("", "--notes", "-n"),
) -> None:
    """Record a human label on a pending audit."""
    conn = _open_conn()
    try:
        queue = AuditQueue(conn)
        try:
            updated = queue.submit(audit_id, human_label, notes, reviewer)
        except KeyError as e:
            typer.echo(f"not found: {e}", err=True)
            raise typer.Exit(code=2) from e
        except ValueError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"{updated.audit_id} -> {updated.human_label}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# autonomy promote
# ---------------------------------------------------------------------------


@autonomy_app.command("promote")
def autonomy_promote(
    agent: str = typer.Argument(...),
    task_class: str = typer.Argument(...),
    tier: str = typer.Option(..., "--tier"),
    operator: str = typer.Option(..., "--operator"),
    reason: str = typer.Option("manual_promotion", "--reason"),
) -> None:
    """Promote an (agent, task_class) to a new tier. Requires an operator id."""
    try:
        new_tier = AutonomyTier(tier)
    except ValueError as e:
        typer.echo(f"invalid tier: {tier}", err=True)
        raise typer.Exit(code=2) from e

    conn = _open_conn()
    try:
        registry = _open_registry(conn)
        events = WideEventStore(conn, clock=RealClock())
        controller = AutonomyController(conn, events, registry_store=registry)
        try:
            change = controller.apply_promotion(agent, task_class, new_tier, reason, operator)
        except ValueError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(
            f"{change.agent_id}/{change.task_class}: {change.old_tier.value} -> {change.new_tier.value}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


@app.command()
def verify(
    db: Path = typer.Option(None, "--db", help="SQLite path; default = settings.db_path."),
) -> None:
    """Verify the wide-event chain integrity for every run."""
    conn = _open_conn(db)
    try:
        results = verify_all(conn)
        if not results:
            typer.echo("no runs to verify")
            return
        broken = [rid for rid, ok in results.items() if not ok]
        for rid, ok in results.items():
            typer.echo(f"{'OK   ' if ok else 'BREAK'} {rid}")
        typer.echo(f"{len(results)} run(s), {len(broken)} broken.")
        if broken:
            raise typer.Exit(code=1)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


@app.command()
def dashboard(port: int = typer.Option(8080)) -> None:
    """Open the local ACP dashboard in your browser."""
    url = f"http://localhost:{port}/dashboard"
    typer.echo(f"opening {url}")
    try:
        webbrowser.open(url)
    except Exception:
        # Headless / CI — just print so tests can assert on stdout.
        pass


def main() -> int:
    app()
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["app", "main"]
