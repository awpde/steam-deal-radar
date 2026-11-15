@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 依次尝试：PATH 里的 python → py 启动器 → 常见安装目录
set "PY="
for %%i in (python.exe) do @if not "%%~$PATH:i"=="" set "PY=%%~$PATH:i"
if not defined PY for %%i in (py.exe) do @if not "%%~$PATH:i"=="" set "PY=%%~$PATH:i"
if not defined PY if exist "D:\Python\python.exe" set "PY=D:\Python\python.exe"
if not defined PY if exist "C:\Python313\python.exe" set "PY=C:\Python313\python.exe"
if not defined PY for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do @if exist "%%~d\python.exe" set "PY=%%~d\python.exe"

if not defined PY goto nopython

rem 检查版本：需要 3.10 或更高（太老的版本跑不了）
"%PY%" -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 goto oldpython

"%PY%" run.py %*
if errorlevel 1 (
  echo.
  echo  程序异常退出，把上面的错误信息截图反馈即可。
  pause
)
endlocal
exit /b 0

:nopython
echo.
echo  [x] 没有找到 Python。
echo.
echo      请先安装 Python 3.10 或更高版本： https://www.python.org/downloads/
echo      安装时务必勾选 "Add Python to PATH"，装完再双击本文件。
echo.
pause
endlocal
exit /b 1

:oldpython
echo.
echo  [x] 找到的 Python 版本太旧，需要 3.10 或更高。
echo      当前： %PY%
echo      请到 https://www.python.org/downloads/ 装一个新版本。
echo.
pause
endlocal
exit /b 1
