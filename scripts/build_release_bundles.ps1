$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

python .\scripts\build_release_bundles.py @args
if ($LASTEXITCODE -ne 0) {
    throw "build_release_bundles.py failed with exit code $LASTEXITCODE"
}
