# MAS

MAS brings your local multi-agent software intelligence system into VS Code.

## What It Does

MAS gives you:

- a chat-first workspace inside VS Code
- one-click local runtime setup
- one-click API start and health checks
- repository analysis for the open workspace
- LLM connection with your own API key

The goal is simple:

`connect llm -> type in english -> MAS handles the rest`

## Core Flow

1. Open the `MAS` activity bar icon
2. Click `connect llm`
3. Choose your provider and paste your API key
4. Click `setup` if the local runtime is not ready
5. Click `start api`
6. Ask MAS what you want in English

Examples:

- `connect to ChatGPT and use gpt-4.1`
- `start the api and check health`
- `analyze this workspace`
- `summarize the latest task`

## Supported Providers

- ChatGPT / OpenAI
- Claude / Anthropic
- DeepSeek
- Kimi / Moonshot
- OpenRouter
- Other OpenAI-compatible APIs

API keys are stored in VS Code secret storage.

## Commands

- `MAS: Open Panel`
- `MAS: Install Runtime`
- `MAS: Start API`
- `MAS: Health Check`
- `MAS: Analyze Current Workspace`
- `MAS: Connect To LLM`
- `MAS: Show Last Task`
- `MAS: Refresh Sidebar`

## Configuration

- `masi.apiBaseUrl`
- `masi.repoRoot`
- `masi.pythonPath`

Usually you can leave these alone and let MAS auto-detect the repo.

## Best Use Case

MAS works best when you want to:

- inspect a codebase locally
- analyze architecture and violations
- use natural language to drive local agent workflows
- connect your own LLM instead of using a built-in hosted account system

## Local Runtime

If you are developing MAS locally, install the runtime from the repo root:

```powershell
.\run_full_system.ps1 -TargetPath "C:\path\to\target\repo"
```

Or manually:

```powershell
docker compose up -d
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv312\Scripts\python.exe -m uvicorn src.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

## Publishing

Marketplace publishing notes are in:

- `PUBLISHING.md`
