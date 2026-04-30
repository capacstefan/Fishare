"""Build p2p_native.dll.

Tries compilers in order:
  1. MSVC (cl.exe) via VS 2022 / Build Tools — preferred.
  2. MinGW-w64 (g++) — fallback, often available via Git for Windows extras,
     Chocolatey, MSYS2, or standalone installers.

The resulting DLL is placed next to this script AND next to the package root
so ctypes finds it automatically on import.

Usage (from anywhere inside the project):
    py native\\build.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC  = HERE / "p2p_native.cpp"
DLL  = HERE / "p2p_native.dll"
# Also copy the DLL here so ctypes finds it on import without PATH tricks.
PKG_DIR = HERE.parent


def _copy_to_pkg() -> None:
    if PKG_DIR.is_dir():
        dest = PKG_DIR / DLL.name
        shutil.copy2(DLL, dest)
        print(f"[build] copied -> {dest}")


# ---------------------------------------------------------------------------
# Strategy 1: MSVC
# ---------------------------------------------------------------------------
def _try_msvc() -> bool:
    pf = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    vswhere = pf / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.exists():
        print("[build] vswhere.exe not found — MSVC unavailable.")
        return False

    out = subprocess.check_output(
        [str(vswhere), "-latest", "-products", "*",
         "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
         "-property", "installationPath"],
        text=True,
    ).strip()
    if not out:
        print("[build] MSVC C++ toolset not found (no 'Desktop C++' workload). Trying MinGW...")
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
    rc = subprocess.call(cmd, shell=True)
    if rc != 0:
        return False

    # Remove MSVC side-effect files.
    for ext in (".obj", ".exp", ".lib"):
        p = DLL.with_suffix(ext)
        if p.exists():
            try: p.unlink()
            except OSError: pass
    return True


# ---------------------------------------------------------------------------
# Strategy 2: MinGW g++
# ---------------------------------------------------------------------------
def _try_mingw() -> bool:
    gpp = shutil.which("g++")
    if not gpp:
        print("[build] g++ not found on PATH — MinGW unavailable.")
        return False

    cmd = [
        gpp,
        "-shared", "-O2", "-o", str(DLL),
        "-Wl,--out-implib," + str(DLL.with_suffix(".lib")),
        str(SRC),
    ]
    print(f"[build] MinGW: {' '.join(cmd)}")
    rc = subprocess.call(cmd, cwd=str(HERE))
    if rc != 0:
        return False

    # Remove MinGW import lib (not needed by ctypes).
    imp = DLL.with_suffix(".lib")
    if imp.exists():
        try: imp.unlink()
        except OSError: pass
    return True


# ---------------------------------------------------------------------------
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
        "     https://aka.ms/vs/17/release/vs_BuildTools.exe\n"
        "  B) MinGW-w64 via MSYS2:  https://www.msys2.org/\n"
        "     pacman -S mingw-w64-x86_64-gcc\n"
        "     (add C:\\msys64\\mingw64\\bin to PATH, then re-run)\n"
        "  C) MinGW-w64 via Chocolatey (admin prompt):\n"
        "     choco install mingw\n"
        "\nThe app works without the DLL (falls back to hashlib.sha256 automatically)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
