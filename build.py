"""Build a standalone executable using PyInstaller (Windows & Linux).

Run from the project root:

    python build.py

Produces a single-file, windowed application:

    Windows -> dist/Fishare.exe
    Linux   -> dist/Fishare

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
PKG_DIR = ROOT_DIR / "fishare"
PKG_NAME = PKG_DIR.name  # "fishare"
NATIVE_BUILD = ROOT_DIR / "native" / "build.py"
APP_NAME = "Fishare"

ENTRY_SCRIPT = ROOT_DIR / "_pyinstaller_entry.py"
LIB_NAME = "p2p_native.dll" if sys.platform == "win32" else "libp2p_native.so"
ICON_PNG = PKG_DIR / "assets" / "logo.png"
ICON_ICO = PKG_DIR / "assets" / "logo.ico"


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


def ensure_icon() -> str | None:
    """Return an icon path for PyInstaller's --icon, building an .ico if needed.

    On Windows a multi-size ``.ico`` is generated from ``logo.png`` via Pillow
    so the produced .exe carries the app logo. Falls back to the PNG (or None)
    if conversion is unavailable.
    """
    if not ICON_PNG.exists():
        return None
    if sys.platform != "win32":
        return str(ICON_PNG)
    try:
        from PIL import Image
    except ImportError:
        print("!! Pillow not found — the .exe will use the default icon.")
        return None
    try:
        img = Image.open(ICON_PNG).convert("RGBA")
        # Pad to a centered, transparent square so Windows renders the icon
        # crisply at every size (icons are expected to be square).
        side = max(img.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        sizes = [(16, 16), (24, 24), (32, 32), (48, 48),
                 (64, 64), (128, 128), (256, 256)]
        square.save(ICON_ICO, format="ICO", sizes=sizes)
        return str(ICON_ICO)
    except Exception as exc:  # pragma: no cover - best-effort icon
        print(f"!! Could not build .ico ({exc}); using default icon.")
        return None


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
    icon = ensure_icon()
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
        # Bundle the logo (and any other assets) next to the package.
        "--add-data", f"{PKG_DIR / 'assets'}{os.pathsep}{PKG_NAME}/assets",
        "--collect-submodules", PKG_NAME,
        "--collect-submodules", "zeroconf",
        "--collect-submodules", "cryptography",
        "--hidden-import", "PyQt6.sip",
    ]
    if icon:
        args += ["--icon", icon]
    args.append(str(ENTRY_SCRIPT))
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
