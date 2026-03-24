# Changelog

## 0.0.7

- Upgraded MAS chat into a more agent-like teammate with structured `summary`, `actions taken`, `files changed`, `code changes`, `suggestions`, and `next step` sections.
- Added workspace-aware diff inspection so MAS can talk about live Git changes and compact code snippets.
- Added file-level and symbol-level inspection prompts such as `explain file ...` and `explain function ...`.
- Added grouped patch planning and a guarded `Apply Approved Edits` flow with confirmation, before/after snippets, and targeted validation reporting.

## 0.0.6

- Added `Connect To LLM` so users can plug MAS into OpenAI, Anthropic, DeepSeek, Kimi, OpenRouter, or another OpenAI-compatible API.
- Added a simpler plain-English operator flow: connect an LLM, type what you want, and let MAS translate it into local actions.
- Improved the MAS chat workspace with a cleaner full-width layout, compact control strip, and better welcome/empty state.
- Fixed local Windows API startup behavior in the extension.

## 0.0.2

- Fixed the MAS sidebar contribution to register as a real webview.
- Added `MAS: Open Panel` for a full editor-style MAS control experience.
- Added a chat-style MAS panel with a prompt box and natural command routing.
- Updated the status bar action to open the MAS control panel directly.

## 0.0.1

- Initial MAS Marketplace package.
- Added MAS branding for the extension, activity bar view, and commands.
- Added `MAS: Install Runtime` to bootstrap the local Python environment.
- Added Marketplace packaging metadata and a PNG extension icon.
