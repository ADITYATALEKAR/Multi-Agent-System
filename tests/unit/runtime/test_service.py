from __future__ import annotations

from typing import TYPE_CHECKING

from src.runtime.service import MASIRuntime

if TYPE_CHECKING:
    from pathlib import Path


def test_submit_analysis_persists_results(tmp_path: Path) -> None:
    runtime = MASIRuntime(state_path=tmp_path / "state.json")

    task = runtime.submit_analysis(repo_path=str(tmp_path), tenant_id="acme")

    assert task.status == "completed"
    assert task.repo_path == str(tmp_path.resolve())
    assert len(task.work_items) == 5
    assert "repo_summary" in task.result
    assert runtime.get_task(task.task_id) is not None


def test_repair_approval_round_trip(tmp_path: Path) -> None:
    runtime = MASIRuntime(state_path=tmp_path / "state.json")
    repo = tmp_path / "repo"
    repo.mkdir()

    task = runtime.submit_analysis(repo_path=str(repo), tenant_id="acme")
    assert len(task.repairs) >= 1

    approved = runtime.approve_repair(task.repairs[0].repair_id)

    assert approved is not None
    assert approved.status == "approved"


def test_submit_analysis_surfaces_repo_hygiene_findings(tmp_path: Path) -> None:
    runtime = MASIRuntime(state_path=tmp_path / "state.json")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".venv312").mkdir()
    (repo / ".masi_runtime").mkdir()
    (repo / "temp").mkdir()
    (repo / "node_modules").mkdir()
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")
    (repo / "_scratch.txt").write_text("local note", encoding="utf-8")
    (repo / "vscode-extension").mkdir()
    (repo / "vscode-extension" / "sample.vsix").write_text("placeholder", encoding="utf-8")

    task = runtime.submit_analysis(repo_path=str(repo), tenant_id="acme")

    assert task.status == "completed"
    assert len(task.violations) >= 6
    assert len(task.hypotheses) >= 1
    assert len(task.repairs) >= 1
    assert any(item.rule == "repo.hygiene.virtualenv" for item in task.violations)
    assert any(item.rule == "repo.hygiene.extension-artifacts" for item in task.violations)
