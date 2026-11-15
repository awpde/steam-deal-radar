"""把整个工具打包成一个 exe，方便发给别人。

用法（在本目录执行）：
    python build_exe.py

产物：dist/SteamDealRadar.exe —— 单个文件，双击即用，不需要装 Python。
前提：pip install pyinstaller
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "SteamDealRadar"


def main() -> int:
    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            print("[x] 没装 PyInstaller，先执行：pip install pyinstaller")
            return 1

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        # 不要控制台窗口：双击直接出应用窗口，像一个正常的桌面软件
        "--windowed",
        "--name", NAME,
        # 网页界面必须打进去，否则 exe 启动后打不开页面
        "--add-data", f"{ROOT / 'web'}{';' if sys.platform == 'win32' else ':'}web",
        # 内置样本：首次运行又连不上 Steam 时用来占位
        "--add-data", f"{ROOT / 'tests' / 'fixtures'}{';' if sys.platform == 'win32' else ':'}tests/fixtures",
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
    ]
    icon = ROOT / "docs" / "icon.ico"
    if icon.exists():
        args += ["--icon", str(icon)]
    args.append(str(ROOT / "run.py"))
    print("正在打包，第一次会慢一些（1~3 分钟）…")
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        print("[x] 打包失败，看上面的报错")
        return result.returncode

    exe = ROOT / "dist" / f"{NAME}.exe"
    if exe.exists():
        size = exe.stat().st_size / 1024 / 1024
        print(f"\n[√] 完成：{exe}  （{size:.1f} MB）")
        print("    这个文件可以单独拷给别人，双击就能用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
