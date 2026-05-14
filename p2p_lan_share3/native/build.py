"""Build p2p_native.dll using MSVC, falling back to MinGW g++.

Run from anywhere in the project:
    py native\\build.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "p2p_native.cpp"
DLL = HERE / "p2p_native.dll"
PKG = HERE.parent


def _copy_to_pkg() -> None:
    dest = PKG / DLL.name
    shutil.copy2(DLL, dest)
    print(f"[build] copied -> {dest}")


def _try_msvc() -> bool:
    pf = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    vswhere = pf / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.exists():
        print("[build] vswhere.exe not found — MSVC unavailable.")
        return False

    out = subprocess.check_output(
        [str(vswhere), "-latest", "-products", "*",
         "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
         "-property", "installationPath"], text=True,
    ).strip()
    if not out:
        print("[build] MSVC C++ toolset not found. Trying MinGW...")
        return False

    vcvars = Path(out) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not vcvars.exists():
        print(f"[build] vcvars64.bat missing under {out}. Trying MinGW...")
        return False

    cmd = (
        f'call "{vcvars}" >nul && '
        f'cd /d "{HERE}" && '
        f'cl /LD /O2 /EHsc /nologo /utf-8 "{SRC.name}" /Fe:"{DLL.name}"'
    )
    print(f"[build] MSVC: {cmd}")
    if subprocess.call(cmd, shell=True) != 0:
        return False
    for ext in (".obj", ".exp", ".lib"):
        p = DLL.with_suffix(ext)
        if p.exists():
            try: p.unlink()
            except OSError: pass
    return True


def _try_mingw() -> bool:
    gpp = shutil.which("g++")
    if not gpp:
        print("[build] g++ not found on PATH — MinGW unavailable.")
        return False
    cmd = [gpp, "-shared", "-O2", "-o", str(DLL),
           "-Wl,--out-implib," + str(DLL.with_suffix(".lib")), str(SRC)]
    print(f"[build] MinGW: {' '.join(cmd)}")
    if subprocess.call(cmd, cwd=str(HERE)) != 0:
        return False
    imp = DLL.with_suffix(".lib")
    if imp.exists():
        try: imp.unlink()
        except OSError: pass
    return True


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("This builder targets Windows (MSVC or MinGW).")
    if not SRC.exists():
        raise SystemExit(f"[build] source not found: {SRC}")

    if _try_msvc() or _try_mingw():
        _copy_to_pkg()
        print(f"[build] OK: {DLL}")
        return 0

    print(
        "\n[build] No compiler found. Install one of:\n"
        "  A) Visual Studio 2022 Build Tools with 'Desktop development with C++'\n"
        "  B) MinGW-w64 (MSYS2 / Chocolatey)\n"
        "\nThe DLL is required — the app will not start without it."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
