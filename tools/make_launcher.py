"""生成 启动.bat（GBK 编码 + CRLF 换行）。

为什么需要这个脚本？
Windows 的 cmd.exe 有两个硬性要求：
1. 换行必须是 CRLF，纯 LF 会让它把命令截断成半截（'set' 变成 'et'）
2. 中文必须用系统代码页（简中是 GBK/936）编码，UTF-8 会显示成乱码并当成命令执行
所以 .bat 不能直接当普通文本写，必须用脚本转一次。
"""

from pathlib import Path

BAT = r"""@echo off
setlocal
cd /d "%~dp0"

set "PY="
for %%i in (python.exe) do if not "%%~$PATH:i"=="" set "PY=%%~$PATH:i"
if not defined PY if exist "D:\Python\python.exe" set "PY=D:\Python\python.exe"
if not defined PY if exist "C:\Python313\python.exe" set "PY=C:\Python313\python.exe"
if not defined PY for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%~d\python.exe" set "PY=%%~d\python.exe"
if not defined PY goto nopython

"%PY%" -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 goto oldpython

echo 正在启动 Steam 雷达，请稍候...
echo.
"%PY%" run.py %*
if errorlevel 1 pause
exit /b 0

:nopython
echo.
echo  [x] 没有找到 Python。
echo.
echo      请先安装 Python 3.10 或更高版本： https://www.python.org/downloads/
echo      安装时务必勾选 "Add Python to PATH"，装完再双击本文件。
echo.
pause
exit /b 1

:oldpython
echo.
echo  [x] 找到的 Python 版本太旧，需要 3.10 或更高。
echo      当前： %PY%
echo      请到 https://www.python.org/downloads/ 安装新版本。
echo.
pause
exit /b 1
"""

target = Path(__file__).resolve().parent.parent / "启动.bat"
# GBK 编码 + CRLF 换行，这是 cmd.exe 唯一能稳定解析的组合
data = BAT.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk")
target.write_bytes(data)
print(f"已写入 {target}  {len(data)} 字节  CRLF={data.count(b'\r\n')} 行")
