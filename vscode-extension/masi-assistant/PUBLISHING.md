# Publishing MAS

## Before You Publish

Update these fields in [package.json](./package.json):

- `publisher`: replace `aditya-local` with your real Marketplace publisher ID
- `repository`: add your public Git repository URL
- `homepage`: add your product or documentation URL
- `bugs.url`: add your issue tracker URL

Recommended example:

```json
"publisher": "your-publisher-id",
"repository": {
  "type": "git",
  "url": "https://github.com/your-org/mas-agent.git"
},
"homepage": "https://github.com/your-org/mas-agent#readme",
"bugs": {
  "url": "https://github.com/your-org/mas-agent/issues"
}
```

## Marketplace Steps

1. Create an Azure DevOps organization if you do not already have one.
2. Create a Personal Access Token with the Marketplace `Manage` scope.
3. Create a publisher in Visual Studio Marketplace.
4. Log in locally with `npx vsce login <your-publisher-id>`.
5. Update `package.json` with that same publisher ID.
6. Package with `npx vsce package`.
7. Publish with `npx vsce publish` or upload the generated VSIX in the Marketplace publisher portal.

## Local Checks

Run these commands from the extension folder before publishing:

```powershell
npm install
npm run compile
npx vsce package
```

## Notes

- The extension icon used for Marketplace publication must be a PNG, not an SVG.
- `README.md` and `CHANGELOG.md` should use HTTPS image URLs if you later add screenshots.
- `vscode:prepublish` already compiles the extension automatically during packaging.
