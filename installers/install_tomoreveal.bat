@echo off
rem TomoReveal installer for Windows 10/11, for computers without Conda.
rem Double-click this file inside the downloaded TomoReveal folder (installers\install_tomoreveal.bat).
rem Everything goes into %LOCALAPPDATA%\TomoReveal; delete that folder and the shortcuts to uninstall.
setlocal
set "APP=%LOCALAPPDATA%\TomoReveal"
set "ROOT=%~dp0.."
if not exist "%ROOT%\environment.yml" (
  echo Run this from the complete TomoReveal folder ^(environment.yml is missing^).
  pause & exit /b 1
)
if not exist "%APP%" mkdir "%APP%"
if not exist "%APP%\micromamba.exe" (
  echo [1/3] Downloading the package manager ^(micromamba, about 15 MB^) ...
  curl -fsSL "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64" -o "%APP%\micromamba.exe" || goto :fail
)
if not exist "%APP%\env\python.exe" (
  echo [2/3] Installing Python, Qt and the 3D/video libraries ^(about 4 GB, several minutes^) ...
  "%APP%\micromamba.exe" create -y -q -r "%APP%\mamba-root" -p "%APP%\env" -f "%ROOT%\environment.yml" || goto :fail
) else (
  rem an update, or a first install that was stopped part way: bring the libraries to what this version needs
  echo [2/3] Updating the libraries to what this version needs ...
  "%APP%\micromamba.exe" install -y -q -r "%APP%\mamba-root" -p "%APP%\env" -f "%ROOT%\environment.yml" || goto :fail
)
echo [3/3] Installing the program and shortcuts ...
"%APP%\env\python.exe" -m pip install -q --no-deps --upgrade --force-reinstall "%ROOT%" || goto :fail
"%APP%\env\python.exe" -m tomoreveal icon "%APP%\tomoreveal.ico" >nul || goto :fail
rem The folder is read from APP inside PowerShell, so a name such as O'Brien cannot break the script.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop'; $a = $env:APP; $w = New-Object -ComObject WScript.Shell;" ^
  "foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {" ^
  "  $s = $w.CreateShortcut((Join-Path $d 'TomoReveal.lnk'));" ^
  "  $s.TargetPath = (Join-Path $a 'env\pythonw.exe'); $s.Arguments = '-m tomoreveal';" ^
  "  $s.IconLocation = (Join-Path $a 'tomoreveal.ico'); $s.WorkingDirectory = $env:USERPROFILE; $s.Save() }" || goto :fail
echo.
echo Done. Double-click TomoReveal on your desktop or in the Start menu.
pause
exit /b 0
:fail
echo.
echo Installation failed. Check your internet connection and run the installer again.
pause
exit /b 1
