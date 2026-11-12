# Packs dist/tab-dispatch-<version>.vsix with a pinned vsce.
# Usage: powershell -File build.ps1   (needs network the first time to fetch vsce into the npm cache)
$ErrorActionPreference = 'Stop'
$VsceVersion = '3.2.1'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$version = (Get-Content (Join-Path $root 'package.json') -Raw | ConvertFrom-Json).version
New-Item -ItemType Directory -Force -Path (Join-Path $root 'dist') | Out-Null
$out = Join-Path $root "dist\tab-dispatch-$version.vsix"
& npx --yes "@vscode/vsce@$VsceVersion" package --allow-missing-repository --skip-license --no-dependencies -o $out
if ($LASTEXITCODE -ne 0) { throw "vsce failed with exit $LASTEXITCODE" }
Write-Output "OK $out"
