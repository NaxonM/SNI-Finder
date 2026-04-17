@echo off
setlocal
cd /d "%~dp0"

python -c "from sni_finder.settings import load_settings; import sys; s = load_settings(); sys.exit(0 if str(getattr(s, 'vless_source', '')).strip() else 1)"
if errorlevel 1 (
	echo VLESS source is not configured in config\scanner_settings.json.
	echo Running interactive setup...
	python scanner.py configure
	if errorlevel 1 (
		echo.
		echo Configuration failed or was cancelled.
		set EXITCODE=%ERRORLEVEL%
		echo Log file: logs\scanner.log
		pause
		exit /b %EXITCODE%
	)
	python -c "from sni_finder.settings import load_settings; import sys; s = load_settings(); sys.exit(0 if str(getattr(s, 'vless_source', '')).strip() else 1)"
	if errorlevel 1 (
		echo.
		echo vless_source is still empty. Please set it and relaunch.
		echo Log file: logs\scanner.log
		pause
		exit /b 1
	)
)

python scanner.py
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" (
	echo Scanner exited with an error. Code=%EXITCODE%
) else (
	echo Scanner closed.
)
echo Log file: logs\scanner.log
pause
exit /b %EXITCODE%
endlocal
