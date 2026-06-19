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
:: Locate the shared menu: alongside this wrapper (distributed) or in the monorepo (m3trik).
set "generic=%~dp0package-manager.bat"
if not exist "%generic%" set "generic=%~dp0..\..\..\m3trik\package-manager.bat"
IF NOT EXIST "%generic%" (
    powershell -NoProfile -Command "Write-Host '  [!!] Shared package-manager.bat not found next to this wrapper or in m3trik.' -ForegroundColor DarkRed"
    timeout /t 3 >nul
    ENDLOCAL
    exit /b 1
)
call "%generic%" "%blenderpy%" "Blender %blender_version%" "blender%blender_version%"
ENDLOCAL
exit /b %ERRORLEVEL%
