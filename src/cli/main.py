"""MASI - Multi-Agent Software Intelligence System CLI."""
from __future__ import annotations

import logging

import structlog
import typer

from src.runtime import get_runtime

app = typer.Typer(name="masi", help="MASI - Multi-Agent Software Intelligence System CLI")


def _configure_cli_logging() -> None:
    if getattr(_configure_cli_logging, "_done", False):
        return
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    _configure_cli_logging._done = True


@app.command()
def version() -> None:
    """Print the system version."""
    typer.echo("masi 1.0.0")


@app.command()
def analyze(
    path: str = ".",
    tenant: str = "default",
) -> None:
    """Analyze a repository for architectural violations."""
    _configure_cli_logging()
    runtime = get_runtime()
    task = runtime.submit_analysis(path, tenant_id=tenant)
    typer.echo(f"Analyzing {task.repo_path} for tenant {tenant}...")
    typer.echo(f"Task ID: {task.task_id}")
    typer.echo(f"Analysis complete. {len(task.violations)} violations found.")


@app.command()
def violations(
    tenant: str = "default",
) -> None:
    """List architectural violations."""
    _configure_cli_logging()
    runtime = get_runtime()
    items = runtime.list_violations(tenant)
    typer.echo(f"Violations for tenant {tenant}:")
    if not items:
        typer.echo("No violations found.")
        return
    for violation in items:
        typer.echo(
            f"- [{violation.severity}] {violation.rule} :: {violation.file_path} :: {violation.message}"
        )


@app.command()
def hypotheses(
    task_id: str = "none",
) -> None:
    """List hypotheses for a given task."""
    _configure_cli_logging()
    runtime = get_runtime()
    task = runtime.get_task(task_id)
    typer.echo(f"Hypotheses for task {task_id}:")
    if task is None or not task.hypotheses:
        typer.echo("No hypotheses generated.")
        return
    for hypothesis in task.hypotheses:
        typer.echo(f"- {hypothesis.title}: {hypothesis.summary}")


@app.command()
def repair(
    task_id: str = "none",
) -> None:
    """Show repair candidates for a given task."""
    _configure_cli_logging()
    runtime = get_runtime()
    repairs = runtime.get_repairs(task_id)
    typer.echo(f"Repair candidates for task {task_id}:")
    if not repairs:
        typer.echo("No repair candidates available.")
        return
    for item in repairs:
        typer.echo(f"- {item.repair_id} [{item.status}] {item.description}")


@app.command()
def approve(
    repair_id: str = "none",
    environment: str = "staging",
) -> None:
    """Approve a repair for execution."""
    _configure_cli_logging()
    runtime = get_runtime()
    repair_item = runtime.approve_repair(repair_id)
    typer.echo(f"Approving repair {repair_id} for environment {environment}...")
    if repair_item is None:
        typer.echo("Repair not found.")
        raise typer.Exit(code=1)
    typer.echo("Repair approved and queued for execution.")


@app.command()
def status() -> None:
    """Show system status."""
    _configure_cli_logging()
    runtime = get_runtime()
    summary = runtime.status()
    typer.echo(f"System: {summary['system']}")
    typer.echo(f"Agents: {summary['agents']}")
    typer.echo(f"Triage: {summary['triage']}")
    typer.echo(f"Stored tasks: {summary['stored_tasks']}")


@app.command()
def health() -> None:
    """Health check."""
    _configure_cli_logging()
    runtime = get_runtime()
    typer.echo(runtime.health()["message"])


if __name__ == "__main__":
    app()
