from __future__ import annotations

"""Unit tests for the Phase 6 CLI layer using Typer's CliRunner."""

import pytest

from typer.testing import CliRunner
from src.cli.main import app

runner = CliRunner()


def test_version():
    """'version' command prints 'masi 1.0.0'."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "masi 1.0.0" in result.output


def test_health():
    """'health' command prints 'healthy'."""
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 0
    assert "healthy" in result.output


def test_status():
    """'status' command prints 'Agents: 10'."""
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Agents: 10" in result.output


def test_analyze():
    """'analyze' with defaults prints 'Analyzing'."""
    result = runner.invoke(app, ["analyze"])
    assert result.exit_code == 0
    assert "Analyzing" in result.output


def test_violations():
    """'violations' command prints 'Violations'."""
    result = runner.invoke(app, ["violations"])
    assert result.exit_code == 0
    assert "Violations" in result.output
