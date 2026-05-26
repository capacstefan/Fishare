"""Build a standalone Windows executable using PyInstaller.

Run from the project root:

    py build_exe.py

Produces `dist/P2P LAN Share.exe`. The executable is a single file,
windowed (no console), and bundles the native DLL, PyQt6, zeroconf,
cryptography and all other runtime dependencies. It can be copied to
another Windows machine and launched without installing Python.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
PKG_DIR = ROOT_DIR / "p2p_lan_share"
PKG_NAME = PKG_DIR.name  # "p2p_lan_share"
APP_NAME = "P2P LAN Share"

ENTRY_SCRIPT = ROOT_DIR / "_pyinstaller_entry.py"
DLL_NAME = "p2p_native.dll"


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print(">> Installing PyInstaller...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"])


def write_entry_script() -> None:
    ENTRY_SCRIPT.write_text(
        "import sys\n"
        f"from {PKG_NAME}.main import main\n"
        "sys.exit(main())\n",
        encoding="utf-8",
    )


def clean() -> None:
    for d in ("build", "dist"):
        p = ROOT_DIR / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    spec = ROOT_DIR / f"{APP_NAME}.spec"
    if spec.exists():
        spec.unlink()


def build() -> int:
    dll = PKG_DIR / DLL_NAME
    if not dll.exists():
        print(
            f"!! {DLL_NAME} not found in {PKG_DIR}. "
            "Build it first: py p2p_lan_share/native/build.py"
        )
        return 1

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--paths", str(ROOT_DIR),
        # Place the DLL inside the bundled package dir so native.py finds it.
        "--add-binary", f"{dll}{';'}{PKG_NAME}",
        "--collect-submodules", PKG_NAME,
        "--collect-submodules", "zeroconf",
        "--collect-submodules", "cryptography",
        "--hidden-import", "PyQt6.sip",
        str(ENTRY_SCRIPT),
    ]
    print(">>", " ".join(args))
    return subprocess.call(args, cwd=str(ROOT_DIR))


def main() -> int:
    ensure_pyinstaller()
    write_entry_script()
    clean()
    try:
        rc = build()
    finally:
        try:
            ENTRY_SCRIPT.unlink()
        except OSError:
            pass
    if rc == 0:
        out = ROOT_DIR / "dist" / f"{APP_NAME}.exe"
        print(f"\n[OK] Built: {out}")
    else:
        print("\n[FAIL] PyInstaller returned", rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
