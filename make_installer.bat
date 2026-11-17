@echo off
rem Builds dist\QuickLauncher.exe and the Setup installer (dist\QuickLauncher-Setup-<version>.exe).
rem Needs Inno Setup 6:  winget install JRSoftware.InnoSetup
setlocal
pushd "%~dp0" >nul

call "%~dp0make_exe.bat"
if errorlevel 1 (
    popd
    exit /b 1
)

".venv\Scripts\python.exe" "%~dp0scripts\build_installer.py" --no-build
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
