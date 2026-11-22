# Installs the privacy pre-commit and commit-msg hooks (LF, no BOM).
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$gitDir = (& git -C $repo rev-parse --absolute-git-dir).Trim()
$hooks = Join-Path $gitDir "hooks"
New-Item -ItemType Directory -Force -Path $hooks | Out-Null
$enc = New-Object System.Text.UTF8Encoding($false)

$pre = "#!/bin/sh`nPY=.venv/Scripts/python`n[ -x `"`$PY`" ] || PY=python`n`"`$PY`" scripts/check_private.py --staged`nrc=`$?`nif [ `$rc -ne 0 ]; then echo `"Privacy check failed - commit blocked`"; fi`nexit `$rc`n"
[System.IO.File]::WriteAllText((Join-Path $hooks "pre-commit"), $pre, $enc)

$msg = "#!/bin/sh`nPY=.venv/Scripts/python`n[ -x `"`$PY`" ] || PY=python`n`"`$PY`" scripts/check_private.py --message-file `"`$1`"`nrc=`$?`nif [ `$rc -ne 0 ]; then echo `"Privacy check failed - commit message blocked`"; fi`nexit `$rc`n"
[System.IO.File]::WriteAllText((Join-Path $hooks "commit-msg"), $msg, $enc)

Write-Host "Installed hooks in $hooks"
