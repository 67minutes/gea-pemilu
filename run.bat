@echo off
rem Start the local election site and open it in the default browser.
rem Uses the database in .env (local PostgreSQL on 127.0.0.1:55432). Close this window to stop the server.
setlocal
cd /d "%~dp0"

echo Starting local PostgreSQL...
uv run python scripts/windows_postgres.py start || goto :error

echo Preparing database and translations...
uv run python scripts/compile_translations.py || goto :error
uv run python manage.py migrate --noinput -v 0 || goto :error

rem Open the browser once the server answers, without blocking the server start.
start "" /b powershell -NoProfile -Command "for ($i = 0; $i -lt 60; $i++) { try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health/live/ | Out-Null; Start-Process 'http://127.0.0.1:8000/'; break } catch { Start-Sleep -Milliseconds 500 } }"

echo Serving at http://127.0.0.1:8000/  (Ctrl+C to stop)
uv run python manage.py runserver 127.0.0.1:8000
goto :eof

:error
echo.
echo Startup failed. See the messages above.
pause
exit /b 1
