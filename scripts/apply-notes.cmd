@echo off
mykr-ops notes --apply
if errorlevel 1 echo.
if errorlevel 1 echo mykr-ops returned an error.
echo.
pause
