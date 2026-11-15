@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 找 Python：优先用系统里已装的，找不到再看 py 启动器
set "PY="
for %%i in (python.exe) do @if not "%%~$PATH:i"=="" set "PY=%%~$PATH:i"
if not defined PY (
  for %%i in (py.exe) do @if not "%%~$PATH:i"=="" set "PY=%%~$PATH:i -3"
)
if not defined PY (
  echo.
  echo  [x] 没找到 Python。
  echo      请先安装 Python 3.10 或更高版本： https://www.python.org/downloads/
  echo      安装时记得勾选 "Add Python to PATH"。
  echo.
  pause
  exit /b 1
)

%PY% run.py %*
if errorlevel 1 (
  echo.
  echo  程序异常退出，把上面的错误信息截图反馈即可。
  pause
)
endlocal
