<#
.SYNOPSIS
    Pull the snapshot repository to this machine and audit what arrived.

.DESCRIPTION
    Two jobs on one schedule.

    The pull is the second copy of the only asset in this project that cannot
    be rebuilt. GitHub is the first; if the account or the repository goes,
    this is what is left.

    The audit is the early warning. A collector that stops is silent: the
    workflow simply does not run, nothing sends a failure, and the gap is only
    visible to someone who looks. Running this daily caps how long a dead
    collector can hide at one day.

    Missing a pull costs nothing, unlike missing a collection, which is why
    this one is safe to keep on a laptop that sleeps.

    The checks themselves live in `collector/audit.py` and are not repeated
    here. Two implementations of the same rules in two languages would drift,
    and the Python one is the one with tests.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\pull_snapshots.ps1
#>
[CmdletBinding()]
param(
    # Defaults to the repository this script lives in.
    [string] $RepoPath,

    [string] $LogPath,

    [string] $Python = 'python'
)

$ErrorActionPreference = 'Stop'

# Resolved here rather than as parameter defaults: under [CmdletBinding()],
# Windows PowerShell 5.1 evaluates defaults in a scope where $PSScriptRoot is
# still empty, so `Split-Path -Parent $PSScriptRoot` fails before the first
# statement runs. Verified 2026-08-29 on 5.1.
if (-not $RepoPath) { $RepoPath = Split-Path -Parent $PSScriptRoot }
if (-not $LogPath)  { $LogPath  = Join-Path $PSScriptRoot 'pull_snapshots.log' }

function Write-Log {
    param([string] $Message, [string] $Level = 'INFO')
    $line = '{0}  {1,-5} {2}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK'), $Level, $Message
    Write-Host $line
    Add-Content -Path $LogPath -Value $line -Encoding utf8
}

if (-not (Test-Path (Join-Path $RepoPath '.git'))) {
    Write-Log "$RepoPath is not a git clone - nothing to pull" 'ERROR'
    exit 1
}

Set-Location $RepoPath

# --ff-only rather than a merge or a rebase: this clone is a copy, not a place
# where work happens. If it cannot fast-forward, something is genuinely wrong
# and a script should not paper over it by rewriting local history.
git pull --ff-only
if ($LASTEXITCODE -ne 0) {
    Write-Log 'git pull --ff-only failed - resolve by hand, the copy is stale' 'ERROR'
    exit 1
}
Write-Log 'pulled'

$audit = & $Python -m collector.audit --root $RepoPath 2>&1
$auditExit = $LASTEXITCODE
foreach ($line in $audit) {
    # -cmatch, anchored: PowerShell's -match is case-insensitive, so the audit's
    # own summary line ("0 error(s), 0 warning(s)") would be logged as an error.
    $level = if ($line -cmatch '^\s*ERROR\b') { 'ERROR' }
             elseif ($line -cmatch '^\s*WARN\b') { 'WARN' }
             else { 'INFO' }
    Write-Log $line.ToString().TrimEnd() $level
}

if ($auditExit -ne 0) {
    Write-Log 'audit failed - see the findings above' 'ERROR'
    exit 1
}

exit 0
