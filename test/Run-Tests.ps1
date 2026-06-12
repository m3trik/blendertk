# Runs every blendertk headless suite, each in a FRESH background Blender (session-safety
# rule: never attach to a running instance), and aggregates the ===RESULT=== sentinels.
# Usage: powershell -File blendertk/test/Run-Tests.ps1 [-BlenderExe <path>]
param(
    [string]$BlenderExe = "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
)

if (-not (Test-Path $BlenderExe)) {
    Write-Error "Blender not found: $BlenderExe (pass -BlenderExe)"
    exit 1
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$suites = Get-ChildItem $here -Filter *.py | Sort-Object Name
$failed = @()

foreach ($suite in $suites) {
    $out = & $BlenderExe --background --factory-startup --python $suite.FullName 2>$null | Out-String
    if ($out -match "===RESULT: PASS===") {
        Write-Host "PASS $($suite.Name)"
    } else {
        Write-Host "FAIL $($suite.Name)"
        $failLines = ($out -split "`n") | Where-Object { $_ -match "^FAIL" }
        $failLines | ForEach-Object { Write-Host "     $_" }
        $failed += $suite.Name
    }
}

if ($failed) {
    Write-Host "FAILED suites: $($failed -join ', ')"
    exit 1
}
Write-Host "ALL $($suites.Count) SUITES PASS"
