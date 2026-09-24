# deploy_to_terminal.ps1 -- OTTO EA terminal deployment + shadow-header quarantine
#
# WHY THIS EXISTS
# ---------------
# otto.mq5 uses ANGLE-BRACKET includes (<Otto/*.mqh>). MetaEditor resolves those
# by searching the LIVE terminal's MQL5\Include tree RECURSIVELY, by FILE NAME --
# not against the repo, and not against a %TEMP% staging tree.
#
# Observed 24-09-2026: the repo held a correct v5.31 otto.mq5 (calls
# g_orderManager.BeginOrderCycle() at otto.mq5:730) while MQL5\Include had NO
# Otto\ subfolder at all. Instead LEGACY loose headers sat at Include root
# (COttoOrderManager.mqh v4.80, 27-08-2026) plus older copies inside
# Include\_Otto_backup_20260914-131749\. The name-based recursive search linked
# the v4.80 class, which has no BeginOrderCycle/SafeDeleteOrder member, and the
# compile died with exactly:
#     otto.mq5(730) : error: 'BeginOrderCycle' - undeclared identifier
#     otto.mq5(730) : error: ')' - expression expected
# i.e. a correct source file linked against a 7-release-old header.
#
# build_check.ps1 cannot catch this: it stages a private tree and passes
# /inc:<stage>, so it NEVER reads the terminal Include folder. A manual F7
# compile in MetaEditor always does. This script closes that gap.
#
# WHAT IT DOES
#   1. mirrors the 10 repo modules -> MQL5\Include\Otto\   (unambiguous target)
#   2. copies otto.mq5 -> MQL5\Experts\otto.mq5
#   3. quarantines legacy loose OTTO headers + _Otto_backup_* OUT OF the include
#      search path, so the recursive name search has exactly ONE candidate per
#      module. Destination is MQL5\_legacy_unused\ -- deliberately NOT under
#      Include\, because a quarantine folder inside Include would still be
#      searched recursively and the ambiguity would survive.
#   4. optionally quarantines duplicate entry points (.mq5 only, never .ex5, so
#      a chart currently running OttoEA.ex5 is not disturbed)
#   5. verifies byte-identity source <-> deployed and re-scans for stray copies
#
# Additive and reversible: nothing is deleted, only moved into _legacy_unused\.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File _tools\deploy_to_terminal.ps1
#   ... -QuarantineStaleEntryPoints   # also move duplicate Experts\*.mq5
#   ... -DryRun                       # report only, change nothing

param(
    [string]$Source = (Split-Path -Parent $PSScriptRoot),
    [string]$Terminal = (Join-Path $env:APPDATA "MetaQuotes\Terminal\10CE948A1DFC9A8C27E56E827008EBD4\MQL5"),
    [switch]$QuarantineStaleEntryPoints,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$modules = @(
    "OttoDefines", "COttoNewsFilter", "COttoRiskManager", "COttoMarketStructure",
    "COttoBlockManager", "COttoOrderManager", "COttoCorrelationFilter",
    "COttoTradeManager", "COttoJournal"
)
$entry = "otto.mq5"

Write-Host "=================================================================="
Write-Host "OTTO TERMINAL DEPLOYMENT + SHADOW-HEADER QUARANTINE"
Write-Host "=================================================================="
Write-Host "source  : $Source"
Write-Host "terminal: $Terminal"
if ($DryRun) { Write-Host "mode    : DRY RUN (no changes will be made)" }
Write-Host ""

# --- guards ---------------------------------------------------------------
if (-not (Test-Path -LiteralPath $Source))   { Write-Host "FATAL: source not found: $Source";     exit 2 }
if (-not (Test-Path -LiteralPath $Terminal)) { Write-Host "FATAL: terminal MQL5 not found: $Terminal"; exit 2 }

$missing = @()
foreach ($m in $modules) {
    if (-not (Test-Path -LiteralPath (Join-Path $Source "$m.mqh"))) { $missing += "$m.mqh" }
}
if (-not (Test-Path -LiteralPath (Join-Path $Source $entry))) { $missing += $entry }
if ($missing.Count -gt 0) {
    Write-Host "FATAL: source does not look like the OTTO repo; missing:"
    $missing | ForEach-Object { Write-Host "  $_" }
    exit 2
}

$stamp = (Select-String -LiteralPath (Join-Path $Source $entry) `
          -Pattern '#property\s+version\s+"([^"]+)"' -List |
          Select-Object -First 1).Matches[0].Groups[1].Value
Write-Host "source entry stamp : otto.mq5 = $stamp"

$incRoot  = Join-Path $Terminal "Include"
$incOtto  = Join-Path $Terminal "Include\Otto"
$experts  = Join-Path $Terminal "Experts"
$legacyInc = Join-Path $Terminal "_legacy_unused\Include"
$legacyExp = Join-Path $Terminal "_legacy_unused\Experts"

if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $incOtto, $experts, $legacyInc, $legacyExp | Out-Null
}

# --- Step 1: deploy OTTO modules into the unambiguous Include\Otto target ----
Write-Host ""
Write-Host "------------------------------------------------------------------"
Write-Host "STEP 1: deploy modules -> Include\Otto"
Write-Host "------------------------------------------------------------------"
$deployed = 0
foreach ($m in ($modules + "OttoDefines") | Select-Object -Unique) {
    $src = Join-Path $Source "$m.mqh"
    $dst = Join-Path $incOtto "$m.mqh"
    if (-not (Test-Path -LiteralPath $src)) { continue }
    if ($DryRun) {
        Write-Host ("  [dry] {0,-26} -> {1}" -f "$m.mqh", $dst)
    } else {
        Copy-Item -LiteralPath $src -Destination $dst -Force
    }
    $deployed++
}
Write-Host ("  modules deployed: {0}" -f $deployed)

# --- Step 2: deploy the entry point -----------------------------------------
Write-Host ""
Write-Host "------------------------------------------------------------------"
Write-Host "STEP 2: deploy entry point -> Experts\otto.mq5"
Write-Host "------------------------------------------------------------------"
$srcEntry = Join-Path $Source $entry
$dstEntry = Join-Path $experts $entry
if ($DryRun) {
    Write-Host ("  [dry] {0} -> {1}" -f $srcEntry, $dstEntry)
} else {
    # Explicit file name: Copy-Item -Destination with a directory path would
    # build a concatenated name like "Experts\otto.mq5\otto.mq5".
    Copy-Item -LiteralPath $srcEntry -Destination (Join-Path $experts $entry) -Force
    Write-Host ("  copied: {0}" -f $dstEntry)
}

# --- Step 3: quarantine shadow headers out of the include search path --------
# MetaEditor's angle-bracket lookup is RECURSIVE BY FILE NAME across
# MQL5\Include. Any second copy of an OTTO module anywhere under that folder is
# a candidate that can win over Include\Otto. Every legacy copy is therefore
# moved OUT of Include entirely -- to MQL5\_legacy_unused\ -- never deleted.
Write-Host ""
Write-Host "------------------------------------------------------------------"
Write-Host "STEP 3: quarantine shadow headers (out of Include\)"
Write-Host "------------------------------------------------------------------"

$quarantined = 0
$skippedLegit = 0

Get-ChildItem -LiteralPath $incRoot -Force | ForEach-Object {
    $item = $_
    $isOttoModule = $false
    foreach ($m in $modules) { if ($item.Name -ieq "$m.mqh") { $isOttoModule = $true } }

    $isBackupDir = $item.PSIsContainer -and ($item.Name -like "_Otto_backup_*")
    $isOttoDir       = $item.PSIsContainer -and ($item.Name -ieq "Otto")
    $isLegacyUnused  = $item.PSIsContainer -and ($item.Name -ieq "_legacy_unused")

    if ($isBackupDir -or ($isOttoModule -and -not $isOttoDir)) {
        $dst = Join-Path $legacyInc $item.Name
        if ($DryRun) {
            Write-Host ("  [dry] QUARANTINE {0,-34} -> {1}" -f $item.Name, $dst)
        } else {
            if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
            Move-Item -LiteralPath $item.FullName -Destination $dst -Force
            Write-Host ("  QUARANTINE {0,-34} -> _legacy_unused\Include\" -f $item.Name)
        }
        $quarantined++
    } elseif ($isOttoDir -or $isLegacyUnused) {
        # expected / our own -- leave alone
    } else {
        $skippedLegit++
    }
}
Write-Host ("  quarantined: {0}   untouched framework entries: {1}" -f $quarantined, $skippedLegit)

# --- Step 4: optional -- retire duplicate OTTO entry points -----------------
# Experts\ held duplicate v5.00-era copies of the OTTO EA: otto.mq5 is canonical,
# while OttoEA.mq5 was a byte-identical v5.00 twin.
#
# SAFETY: this MUST NOT touch unrelated EAs. MQL5\Experts also holds EA.mq5,
# "Ethan .mq5" and TrendSniperEA.mq5, which belong to other projects. Only names
# on the explicit allowlist below are ever moved; a bare "every .mq5 that is not
# otto.mq5" rule would have quarantined three unrelated robots.
# Compiled .ex5 binaries are deliberately left alone so an EA already attached to
# a live chart is not disturbed.
Write-Host ""
Write-Host "------------------------------------------------------------------"
Write-Host "STEP 4: duplicate OTTO entry points"
Write-Host "------------------------------------------------------------------"
$ottoDuplicateAllowlist = @("OttoEA.mq5")
if (-not $QuarantineStaleEntryPoints) {
    Write-Host "  skipped (pass -QuarantineStaleEntryPoints to retire duplicates)"
} else {
    $qEntries = 0
    foreach ($name in $ottoDuplicateAllowlist) {
        $src = Join-Path $experts $name
        if (-not (Test-Path -LiteralPath $src)) {
            Write-Host ("  absent, nothing to do : {0}" -f $name)
            continue
        }
        if ($DryRun) {
            Write-Host ("  [dry] QUARANTINE {0,-34} -> _legacy_unused\Experts\" -f $name)
        } else {
            $dst = Join-Path $legacyExp $name
            if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }
            Move-Item -LiteralPath $src -Destination $dst -Force
            Write-Host ("  QUARANTINE {0,-34} -> _legacy_unused\Experts\" -f $name)
        }
        $qEntries++
    }
    Write-Host ("  duplicate OTTO entry points quarantined: {0}" -f $qEntries)
    Write-Host "  note: unrelated EAs (EA.mq5, Ethan .mq5, TrendSniperEA.mq5) are never touched"
}

if ($DryRun) {
    Write-Host ""
    Write-Host "*** DRY RUN COMPLETE -- nothing was changed ***"
    Write-Host "*** (verification is skipped: the deployed copies do not exist yet) ***"
    exit 0
}

# --- Step 5: verification ---------------------------------------------------
Write-Host ""
Write-Host "=================================================================="
Write-Host "VERIFICATION"
Write-Host "=================================================================="

function Get-Sha([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash
}

$bad = 0
Write-Host "byte-identity source vs deployed:"
foreach ($m in $modules) {
    $s = Get-Sha (Join-Path $Source "$m.mqh")
    $d = Get-Sha (Join-Path $incOtto "$m.mqh")
    if ($null -eq $d) { $verdict = "MISSING"; $bad++ }
    elseif ($s -eq $d) { $verdict = "MATCH" }
    else { $verdict = "*** DIFFER ***"; $bad++ }
    Write-Host ("  {0,-28} {1}" -f "$m.mqh", $verdict)
}
$se = Get-Sha $srcEntry
$de = Get-Sha $dstEntry
if ($null -eq $de) { $ev = "MISSING"; $bad++ }
elseif ($se -eq $de) { $ev = "MATCH" }
else { $ev = "*** DIFFER ***"; $bad++ }
Write-Host ("  {0,-28} {1}" -f $entry, $ev)

# Re-scan the whole Include tree for any surviving OTTO module copy other than
# Include\Otto. This is the single condition that caused the reported error.
Write-Host ""
Write-Host "stray OTTO modules still anywhere under Include\ (must be none):"
$strays = @()
Get-ChildItem -LiteralPath $incRoot -Recurse -File -Filter "*.mqh" -ErrorAction SilentlyContinue |
    ForEach-Object {
        foreach ($m in $modules) {
            if ($_.Name -ieq "$m.mqh") {
                $rel = $_.DirectoryName.Substring($incRoot.Length).TrimStart('\')
                if ($rel -ine "Otto") { $strays += "$rel\$($_.Name)" }
            }
        }
    }
if ($strays.Count -eq 0) {
    Write-Host "  none"
} else {
    $strays | Sort-Object -Unique | ForEach-Object { Write-Host "  $_" }
    $bad += $strays.Count
}

# Prove the linked header now actually declares the symbol that failed.
$momPath = Join-Path $incOtto "COttoOrderManager.mqh"
$hasBegin = Select-String -LiteralPath $momPath -Pattern 'BeginOrderCycle' -Quiet
$hasSafe  = Select-String -LiteralPath $momPath -Pattern 'SafeDeleteOrder'  -Quiet
Write-Host ""
Write-Host ("deployed COttoOrderManager.mqh declares BeginOrderCycle : {0}" -f $hasBegin)
Write-Host ("deployed COttoOrderManager.mqh declares SafeDeleteOrder  : {0}" -f $hasSafe)
if (-not $hasBegin -or -not $hasSafe) { $bad++ }

$dstStamp = (Select-String -LiteralPath $dstEntry -Pattern '#property\s+version\s+"([^"]+)"' -List |
             Select-Object -First 1).Matches[0].Groups[1].Value
Write-Host ("deployed otto.mq5 version stamp : {0}" -f $dstStamp)

Write-Host ""
if ($bad -eq 0 -and $dstStamp -eq $stamp) {
    Write-Host "*** DEPLOY OK: Include\Otto populated, no shadow copies, stamp $stamp ***"
    Write-Host "*** Now compile Experts\otto.mq5 in MetaEditor (F7) to confirm 0 errors ***"
    exit 0
}
Write-Host "*** DEPLOY FAILED: $bad problem(s) above ***"
exit 1
