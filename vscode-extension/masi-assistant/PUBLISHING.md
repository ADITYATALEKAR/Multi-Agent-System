# Publishing MAS 0.0.6

## Current Metadata

The extension is already configured with:

- `publisher`: `FundamentalLabs`
- `name`: `mas-agent`
- version: `0.0.6`
- source repo: `https://github.com/ADITYATALEKAR/Multi-Agent-System.git`

That means the current Marketplace identifier is:

`FundamentalLabs.mas-agent`

## Package The Extension

From `vscode-extension/masi-assistant`:

```powershell
npm.cmd run compile
npx.cmd vsce package
```

This creates:

```text
mas-agent-0.0.6.vsix
```

## Publish To Marketplace

If your PAT is already valid:

```powershell
npx.cmd vsce publish
```

If you need to refresh login first:

```powershell
npx.cmd vsce login FundamentalLabs
npx.cmd vsce publish
```

## PAT Requirements

Create a Personal Access Token with:

- `Organization`: `All accessible organizations`
- `Scope`: `Marketplace -> Manage`

If publish fails with an authorization error, the token is usually expired, tied to the wrong org, or missing the Marketplace `Manage` scope.

## Manual Upload Option

If CLI publishing is inconvenient, you can upload the VSIX manually in the Visual Studio Marketplace publisher portal.

Upload:

- `mas-agent-0.0.6.vsix`

## Pre-publish Checklist

Before publishing:

1. `npm.cmd run compile`
2. confirm `README.md` is public-friendly
3. confirm `CHANGELOG.md` mentions the latest version
4. confirm no local secrets or screenshots with personal data are included

## Notes

- `vscode:prepublish` already compiles automatically during packaging
- the Marketplace listing icon is the PNG file in `media/mas-icon.png`
- API keys are not packaged because MAS stores them in VS Code secret storage, not in the repo
