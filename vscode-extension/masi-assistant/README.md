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

Before publishing to the VS Code Marketplace, replace the temporary local publisher in `package.json` with your real publisher ID and add your real public repository, homepage, and bug tracker URLs. See `PUBLISHING.md` for the exact steps.
