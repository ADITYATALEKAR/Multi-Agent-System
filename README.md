# MAS<img width="1257" height="1723" alt="image" src="https://github.com/user-attachments/assets/da827b40-4e11-46ae-9171-94e7f6049471" />

<img width="1257" height="1723" alt="image" src="https://github.com/user-attachments/assets/7f5ea73c-6483-4732-a2d4-8d9f1ae91161" />
<img width="1760" height="1715" alt="image" src="https://github.com/user-attachments/assets/1e46166e-8832-42cd-8616-a56d17f74095" />

MAS is a multi-agent software intelligence system for analyzing repositories, surfacing architecture issues, and turning plain-English operator requests into concrete local actions.

## What You Get

- VS Code extension with a chat-first UI
- connect your own LLM with an API key
- local MAS backend for analysis and task history
- CLI for health checks and repository analysis
- Docker-backed local infrastructure for Postgres, Neo4j, Redis, and NATS

## Best User Experience

The easiest way to use MAS is:

1. Install the `MAS` VS Code extension
2. Connect your LLM
3. Start the MAS API
4. Type instructions in English

Examples:

- `connect to ChatGPT and use gpt-4.1`
- `start the api`
- `analyze this workspace`
- `summarize the latest task`

## Quick Start

### Option 1: One-command local setup on Windows

From the repo root:

```powershell
.\run_full_system.ps1 -TargetPath "C:\path\to\repo\you\want\to\analyze"
```

This script will:

- create `.venv312` if needed
- install MAS dependencies
- start Docker services
- run migrations
- start the API
- run an analysis

### Option 2: Manual local setup

```powershell
docker compose up -d
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install --upgrade pip
.\.venv312\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv312\Scripts\python.exe -m alembic -c migrations\pg\alembic.ini upgrade head
.\.venv312\Scripts\python.exe -m uvicorn src.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

Check health:

```powershell
.\.venv312\Scripts\blueprint.exe health
.\.venv312\Scripts\blueprint.exe status
```

Run analysis:

```powershell
.\.venv312\Scripts\blueprint.exe analyze --path .
```

## VS Code Extension

The extension lives in `vscode-extension/masi-assistant`.

### Install from VSIX

```powershell
code.cmd --install-extension .\vscode-extension\masi-assistant\mas-agent-0.0.6.vsix --force
```

Then in VS Code:

1. Open the `MAS` activity bar icon
2. Click `connect llm`
3. Paste your API key
4. Click `start api`
5. Start chatting

### Commands

- `MAS: Open Panel`
- `MAS: Install Runtime`
- `MAS: Start API`
- `MAS: Health Check`
- `MAS: Analyze Current Workspace`
- `MAS: Connect To LLM`
- `MAS: Show Last Task`

## LLM Providers

MAS currently supports:

- ChatGPT / OpenAI
- Claude / Anthropic
- DeepSeek
- Kimi / Moonshot
- OpenRouter
- Other OpenAI-compatible providers

Your API key is stored in VS Code secret storage, not in the repo.

## For Other Users

To share MAS with other people, you have two paths:

### Local runtime

Users install the extension and run MAS on their own machine.

Good for:

- developers
- local repository analysis
- private codebases

### Hosted backend

You host the MAS API and let the extension connect to it.

Good for:

- easier onboarding
- centralized updates
- non-technical users

## Public Release Checklist

- GitHub source repo
- VS Code Marketplace extension
- clean README and install guide
- one-command setup script
- optional Docker/self-hosted guide

## Safety

The repo is set up to avoid committing local secrets and runtime data:

- `.env*` ignored
- `.venv*` ignored
- `.masi_runtime/` ignored
- `temp/` ignored
- `*.vsix` ignored
- certificate and key files ignored

## Development Checks

```powershell
.\.venv312\Scripts\lint-imports.exe
.\.venv312\Scripts\python.exe -m ruff check src tests
.\.venv312\Scripts\python.exe -m pytest tests\unit -q
.\.venv312\Scripts\python.exe -m pytest tests\integration -q
```

## Marketplace

Publishing steps for the extension are in:

- `vscode-extension/masi-assistant/PUBLISHING.md`
