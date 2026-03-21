# MAS

This extension installs and connects VS Code to the MAS multi-agent runtime.

## What It Does

MAS adds a dedicated activity bar view for your multi-agent system so users can:

- install the MAS runtime from inside VS Code
- start the local MAS API
- run analysis for the current workspace
- inspect recent tasks, violations, repairs, and hypotheses

## Commands

- `MAS: Install Runtime`
- `MAS: Open Panel`
- `MAS: Start API`
- `MAS: Health Check`
- `MAS: Analyze Current Workspace`
- `MAS: Show Last Task`
- `MAS: Refresh Sidebar`

## Sidebar

Open the `MAS` activity bar icon to access the control panel sidebar. It shows:

- API health
- one-click runtime actions
- recent MAS tasks with clickable summaries

Open `MAS: Open Panel` to launch the full editor-style MAS workspace with a chat-like prompt, quick actions, and natural command routing.

## Expected workflow

1. Run `MAS: Install Runtime` once.
2. Open the target workspace in VS Code.
3. Run `MAS: Start API`.
4. Run `MAS: Analyze Current Workspace`.
5. Review the generated task summary document.

## Configuration

- `masi.apiBaseUrl`
- `masi.repoRoot`
- `masi.pythonPath`

## Publish Notes

The Marketplace package now points to the public MAS source repository and issue tracker. Use `PUBLISHING.md` if you need to publish a new version or rotate publisher credentials later.
