"""Build the native SHA-256 library for the current platform.

    Windows -> p2p_native.dll    (MSVC, falling back to MinGW g++)
    Linux   -> libp2p_native.so  (g++, falling back to clang++)

The compiled library is copied into the ``p2p_lan_share`` package so that
both ``python -m p2p_lan_share.main`` and the PyInstaller bundle can load it
through ``p2p_lan_share/native.py``.

Run from anywhere in the project:

    python native/build.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # <root>/native
SRC = HERE / "p2p_native.cpp"
PKG = HERE.parent / "p2p_lan_share"             # <root>/p2p_lan_share

LIB_NAME = "p2p_native.dll" if sys.platform == "win32" else "libp2p_native.so"
LIB = HERE / LIB_NAME


def _copy_to_pkg() -> None:
    dest = PKG / LIB.name
    shutil.copy2(LIB, dest)
    print(f"[build] copied -> {dest}")


# ---- Windows: MSVC then MinGW -------------------------------------------
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
        f'cl /LD /O2 /EHsc /nologo /utf-8 "{SRC.name}" /Fe:"{LIB.name}"'
    )
    print(f"[build] MSVC: {cmd}")
    if subprocess.call(cmd, shell=True) != 0:
        return False
    for ext in (".obj", ".exp", ".lib"):
        p = LIB.with_suffix(ext)
        if p.exists():
            try: p.unlink()
            except OSError: pass
    return True


def _try_mingw() -> bool:
    gpp = shutil.which("g++")
    if not gpp:
        print("[build] g++ not found on PATH — MinGW unavailable.")
        return False
    cmd = [gpp, "-shared", "-O2", "-o", str(LIB),
           "-Wl,--out-implib," + str(LIB.with_suffix(".lib")), str(SRC)]
    print(f"[build] MinGW: {' '.join(cmd)}")
    if subprocess.call(cmd, cwd=str(HERE)) != 0:
        return False
    imp = LIB.with_suffix(".lib")
    if imp.exists():
        try: imp.unlink()
        except OSError: pass
    return True


# ---- Linux: g++ then clang++ --------------------------------------------
def _try_posix() -> bool:
    for compiler in ("g++", "clang++"):
        cc = shutil.which(compiler)
        if not cc:
            continue
        cmd = [cc, "-shared", "-fPIC", "-O2", "-fvisibility=hidden",
               "-o", str(LIB), str(SRC)]
        print(f"[build] {compiler}: {' '.join(cmd)}")
        if subprocess.call(cmd, cwd=str(HERE)) == 0:
            return True
    print("[build] no C++ compiler found (need g++ or clang++).")
    return False


def main() -> int:
    if not SRC.exists():
        raise SystemExit(f"[build] source not found: {SRC}")

    if sys.platform == "win32":
        ok = _try_msvc() or _try_mingw()
        hint = (
            "\n[build] No compiler found. Install one of:\n"
            "  A) Visual Studio 2022 Build Tools with 'Desktop development with C++'\n"
            "  B) MinGW-w64 (MSYS2 / Chocolatey)\n"
        )
    else:
        ok = _try_posix()
        hint = (
            "\n[build] No compiler found. Install g++ or clang++:\n"
            "  Debian/Ubuntu:  sudo apt install build-essential\n"
            "  Fedora:         sudo dnf install gcc-c++\n"
            "  Arch:           sudo pacman -S gcc\n"
        )

    if ok:
        _copy_to_pkg()
        print(f"[build] OK: {LIB}")
        return 0

    print(hint + "\nThe library is required — the app will not start without it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
