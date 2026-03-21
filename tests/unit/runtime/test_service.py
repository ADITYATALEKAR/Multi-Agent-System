from __future__ import annotations

from pathlib import Path

from src.runtime.service import MASIRuntime


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
