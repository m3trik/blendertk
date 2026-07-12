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
# Suites: test_*.py + the smoke test + the *_slot_check.py slot-wiring harnesses
# (they emit the same ===RESULT=== sentinel). Utility scripts in this dir
# (e.g. dump_runtime_surface.py) don't emit the sentinel and stay excluded.
$suites = Get-ChildItem $here -Filter *.py |
    Where-Object {
        $_.Name -like "test_*.py" -or
        $_.Name -like "*_slot_check.py" -or
        $_.Name -eq "blender_smoke_test.py"
    } |
    Sort-Object Name
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
