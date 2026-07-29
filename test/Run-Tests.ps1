# Thin wrapper around test/run_tests.py -- kept so the documented PowerShell
# invocation keeps working. All logic (fresh-Blender-per-suite, per-check
# counting, README badge) lives in the Python runner, matching every sibling
# package. For scoped runs / flags call the Python runner directly:
#   python blendertk/test/run_tests.py --list
# Usage: powershell -File blendertk/test/Run-Tests.ps1 [-BlenderExe <path>]
param(
    [string]$BlenderExe
)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $here "run_tests.py"

# Prefer the workspace venv (pythontk resolves there); fall back to PATH.
$venvPython = Join-Path (Split-Path -Parent (Split-Path -Parent $here)) ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

$argList = @($runner)
if ($BlenderExe) { $argList += @("--blender", $BlenderExe) }

& $python @argList
exit $LASTEXITCODE
