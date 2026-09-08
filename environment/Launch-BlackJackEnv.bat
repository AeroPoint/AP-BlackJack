@echo off
REM ============================================================================
REM  Blackjack solver -- development shell launcher
REM
REM  Replaces the original Spyder launcher. Differences that matter:
REM    * no hardcoded absolute paths -- it locates the repository from its own
REM      location, so the checkout can live anywhere;
REM    * it opens a shell with the environment active rather than launching one
REM      fixed application; and
REM    * it degrades gracefully when uv is not installed, because the engine has
REM      no dependencies and runs on a bare Python.
REM
REM  First-time setup:  powershell -ExecutionPolicy Bypass -File environment\bootstrap.ps1
REM ============================================================================
setlocal

set "REPO=%~dp0.."
pushd "%REPO%" || (echo Error: cannot enter repository at "%REPO%". & pause & exit /b 1)

where uv >nul 2>&1
if %errorlevel% equ 0 goto :uv

echo.
echo   uv is not installed -- falling back to the bare-Python path.
echo   The engine has no dependencies, so this works; YAML configs will not.
echo   Run environment\bootstrap.ps1 for the full environment.
echo.
set "PYTHONPATH=%REPO%\src;%PYTHONPATH%"
where python >nul 2>&1 || (echo Error: no python on PATH. & pause & popd & exit /b 1)
echo   Try:  python -m blackjack.cli solve --rules vegas6-h17
echo.
cmd /k
goto :done

:uv
echo.
echo   Blackjack solver
echo   ----------------
echo     uv run bj solve --rules vegas6-h17
echo     uv run bj chart --importance
echo     uv run bj explain T6 T
echo     uv run pytest -m "not slow"
echo.
echo   Docs: markdown\ReadMe.md
echo.
cmd /k

:done
popd
endlocal
