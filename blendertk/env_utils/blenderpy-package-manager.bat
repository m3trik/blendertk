@ECHO off
SETLOCAL EnableDelayedExpansion EnableExtensions
:: Blender Python Package Manager (thin wrapper) for Windows.
:: Detects Blender, resolves its bundled python.exe, then hands off to the shared,
:: interpreter-agnostic package-manager.bat (m3trik\package-manager.bat) which owns the
:: menu/operations. Counterpart of mayatk\env_utils\mayapy-package-manager.bat.
:: Usage: blenderpy-package-manager.bat [blender_version]
::   blender_version is optional (e.g. 5.1); if omitted, auto-detects installs under
::   %ProgramFiles%\Blender Foundation\Blender * and prompts for one.
set "preselected_version=%~1"

:setVersion
:: Auto-detect Blender installs (the "scan strategy"). Blender bundles its own Python at
:: <install>\<X.Y>\python\bin\python.exe, where the <X.Y> subdir matches the install version.
set "found_versions="
set "latest_version="
for /f "tokens=*" %%D in ('dir /b /ad "%ProgramFiles%\Blender Foundation\Blender *" 2^>nul') do (
    set "ver_str=%%D"
    set "ver_num=!ver_str:Blender =!"
    if exist "%ProgramFiles%\Blender Foundation\!ver_str!\!ver_num!\python\bin\python.exe" (
        set "found_versions=!found_versions! !ver_num!"
        set "latest_version=!ver_num!"
    )
)

if defined found_versions (
    powershell -NoProfile -Command "Write-Host '  [OK] Detected Blender installations:' -ForegroundColor DarkGreen -NoNewline; Write-Host '%found_versions%' -ForegroundColor DarkYellow"
) else (
    powershell -NoProfile -Command "Write-Host '  [!!] No Blender installations detected in default location' -ForegroundColor DarkRed"
)

ECHO.
if defined preselected_version (
    set "blender_version=%preselected_version%"
    set "preselected_version="
) else if defined latest_version (
    powershell -NoProfile -Command "Write-Host '  Enter Blender version [%latest_version%]: ' -ForegroundColor Gray -NoNewline"
    set "blender_version="
    set /p "blender_version="
    if not defined blender_version set "blender_version=%latest_version%"
) else (
    powershell -NoProfile -Command "Write-Host '  Enter Blender version: ' -ForegroundColor Gray -NoNewline"
    set /p "blender_version="
)
set "blenderpy=%ProgramFiles%\Blender Foundation\Blender %blender_version%\%blender_version%\python\bin\python.exe"

:validateBlenderPyPath
IF EXIST "%blenderpy%" goto handoff
powershell -NoProfile -Command "Write-Host '  [!!] Blender %blender_version% not found' -ForegroundColor DarkRed"
ECHO.
powershell -NoProfile -Command "Write-Host '  Enter full path to Blender python.exe (blank to retry version): ' -ForegroundColor Gray -NoNewline"
set "blenderpy="
set /p "blenderpy="
if not defined blenderpy goto setVersion
goto validateBlenderPyPath

:handoff
:: Locate the shared menu: alongside this wrapper (distributed), in the monorepo
:: (m3trik), or — when this wrapper was downloaded as a single file — fetched from
:: the repo mirror, so one download is enough to bootstrap (curl ships with Win10+).
set "generic=%~dp0package-manager.bat"
if not exist "%generic%" set "generic=%~dp0..\..\..\m3trik\package-manager.bat"
if not exist "%generic%" (
    powershell -NoProfile -Command "Write-Host '  [..] Fetching shared package-manager.bat' -ForegroundColor Gray"
    curl -fsSL -o "%~dp0package-manager.bat" "https://raw.githubusercontent.com/m3trik/blendertk/master/blendertk/env_utils/package-manager.bat" 2>nul
    REM cmd.exe mis-scans LF-only bats - normalize whatever the endpoint served.
    if exist "%~dp0package-manager.bat" powershell -NoProfile -Command "$p = '%~dp0package-manager.bat'; $t = [IO.File]::ReadAllText($p); [IO.File]::WriteAllText($p, ($t -replace '\r', '' -replace '\n', ([char]13 + [char]10)))"
    set "generic=%~dp0package-manager.bat"
)
IF NOT EXIST "%generic%" (
    powershell -NoProfile -Command "Write-Host '  [!!] Shared package-manager.bat not found next to this wrapper or in m3trik, and it could not be downloaded.' -ForegroundColor DarkRed"
    timeout /t 3 >nul
    ENDLOCAL
    exit /b 1
)
call "%generic%" "%blenderpy%" "Blender %blender_version%" "blender%blender_version%"
ENDLOCAL
exit /b %ERRORLEVEL%
