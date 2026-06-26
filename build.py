"""Build a standalone executable using PyInstaller (Windows & Linux).

Run from the project root:

    python build.py

Produces a single-file, windowed application:

    Windows -> dist/P2P LAN Share.exe
    Linux   -> dist/P2P LAN Share

The bundle includes the native library, PyQt6, zeroconf, cryptography and
all other runtime dependencies, so it can be copied to another machine and
launched without installing Python.

If the native library is not already present in the package, it is built
first by invoking ``native/build.py``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
PKG_DIR = ROOT_DIR / "p2p_lan_share"
PKG_NAME = PKG_DIR.name  # "p2p_lan_share"
NATIVE_BUILD = ROOT_DIR / "native" / "build.py"
APP_NAME = "P2P LAN Share"

ENTRY_SCRIPT = ROOT_DIR / "_pyinstaller_entry.py"
LIB_NAME = "p2p_native.dll" if sys.platform == "win32" else "libp2p_native.so"


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print(">> Installing PyInstaller...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"])


def ensure_native() -> bool:
    """Build the native library via native/build.py if it is missing."""
    if (PKG_DIR / LIB_NAME).exists():
        return True
    print(f">> {LIB_NAME} not found — building it via native/build.py ...")
    subprocess.call([sys.executable, str(NATIVE_BUILD)])
    if (PKG_DIR / LIB_NAME).exists():
        return True
    print(f"!! Failed to build {LIB_NAME}. See the messages above.")
    return False


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
    lib = PKG_DIR / LIB_NAME
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--paths", str(ROOT_DIR),
        # Place the library inside the bundled package dir so native.py finds it.
        # os.pathsep is ';' on Windows and ':' on Linux.
        "--add-binary", f"{lib}{os.pathsep}{PKG_NAME}",
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
    if not ensure_native():
        return 1
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
        exe = APP_NAME + (".exe" if sys.platform == "win32" else "")
        print(f"\n[OK] Built: {ROOT_DIR / 'dist' / exe}")
    else:
        print("\n[FAIL] PyInstaller returned", rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
