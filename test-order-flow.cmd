@echo off
setlocal
pushd "%~dp0"
uv run python -m app.order_flow_export.example_client %*
set "CLIENT_EXIT=%ERRORLEVEL%"
popd
exit /b %CLIENT_EXIT%
