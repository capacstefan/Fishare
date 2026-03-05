#!/usr/bin/env python3


"""
build_cpp.py — Build the FIshare C++ engine extension module.

═══════════════════════════════════════════════════════════════════════════════
⚡ QUICK START (If you already have Visual Studio)
═══════════════════════════════════════════════════════════════════════════════

  1. pip install pybind11
  2. Open "Developer Command Prompt for VS 2022" (search in Start menu)
  3. cd <your_project_folder>
  4. python build_cpp.py

  If OpenSSL error:
    → See detailed instructions below for vcpkg or prebuilt OpenSSL

═══════════════════════════════════════════════════════════════════════════════

Usage
-----
    python build_cpp.py          # configure + build (Release)
    python build_cpp.py --debug  # configure + build (Debug)
    python build_cpp.py --clean  # remove build directory, then build

═══════════════════════════════════════════════════════════════════════════════
STEP-BY-STEP BUILD INSTRUCTIONS (Windows)
═══════════════════════════════════════════════════════════════════════════════

OPTION A: Using vcpkg (Recommended - Automatic dependency management)
----------------------------------------------------------------------

  Step 1: Install Visual Studio 2022 (Community Edition is free)
    • Download from: https://visualstudio.microsoft.com/downloads/
    • During installation, select "Desktop development with C++"
    • Ensure these components are checked:
        ✓ MSVC v143 - VS 2022 C++ x64/x86 build tools
        ✓ Windows 10/11 SDK
        ✓ C++ CMake tools for Windows

  Step 2: Install CMake (if not already included with VS)
    • Download from: https://cmake.org/download/
    • Get "Windows x64 Installer"
    • During install, choose "Add CMake to system PATH"

  Step 3: Install pybind11 via pip
    pip install pybind11

  Step 4: Install vcpkg (C++ package manager)
    # Open PowerShell or Command Prompt
    cd C:/
    git clone https://github.com/microsoft/vcpkg
    cd vcpkg
    .\bootstrap-vcpkg.bat

  Step 5: Install OpenSSL via vcpkg
    cd C:/vcpkg
    .\vcpkg install openssl:x64-windows
    # This takes 5-10 minutes, downloads and compiles OpenSSL

  Step 6: Build FIshare C++ Engine
    # Open "Developer Command Prompt for VS 2022"
    # (search in Start menu for "Developer Command Prompt")
    cd <your_project_folder>
    python build_cpp.py --toolchain C:/vcpkg/scripts/buildsystems/vcpkg.cmake

  Done! The cpp_engine.pyd file will be in your project root.


OPTION B: Using Prebuilt OpenSSL (Faster, but manual setup)
------------------------------------------------------------

  Step 1: Install Visual Studio 2022 (same as Option A, Step 1)

  Step 2: Install CMake (same as Option A, Step 2)

  Step 3: Install pybind11 via pip
    pip install pybind11

  Step 4: Download Prebuilt OpenSSL
    • Go to: https://slproweb.com/products/Win32OpenSSL.html
    • Download "Win64 OpenSSL v3.3.x" (NOT the "Light" version)
    • Run installer, install to default location:
        C:/Program Files/OpenSSL-Win64

  Step 5: Set Environment Variable (Temporary - for current session)
    # In Command Prompt or PowerShell:
    set OPENSSL_ROOT_DIR=C:/Program Files/OpenSSL-Win64
    # Or in PowerShell:
    $env:OPENSSL_ROOT_DIR="C:/Program Files/OpenSSL-Win64"

  Step 6: Build FIshare C++ Engine
    # Open "Developer Command Prompt for VS 2022"
    cd <your_project_folder>
    python build_cpp.py

  Done! The cpp_engine.pyd file will be in your project root.


═══════════════════════════════════════════════════════════════════════════════
Linux / macOS
═══════════════════════════════════════════════════════════════════════════════

  Ubuntu/Debian:
    sudo apt install cmake build-essential libssl-dev python3-dev
    pip install pybind11
    python3 build_cpp.py

  macOS:
    brew install cmake openssl
    pip3 install pybind11
    export OPENSSL_ROOT_DIR=$(brew --prefix openssl)
    python3 build_cpp.py

═══════════════════════════════════════════════════════════════════════════════
DEPENDENCIES
═══════════════════════════════════════════════════════════════════════════════

  1. CMake 3.15+         → Build system (auto-detects pybind11 and OpenSSL)
  2. MSVC or g++         → C++ compiler
  3. pybind11            → Python bindings (install via: pip install pybind11)
  4. OpenSSL 1.1.1/3.x   → Cryptography library (install via vcpkg or prebuilt)
  5. Python 3.9+         → Your current Python with development headers

  Note: NO manual project file creation needed - CMake handles everything!

═══════════════════════════════════════════════════════════════════════════════
TROUBLESHOOTING
═══════════════════════════════════════════════════════════════════════════════

  Problem: "cmake not found"
    → Install CMake and add to PATH, or use VS Installer to add CMake tools

  Problem: "cl.exe not found" or "MSVC not found"
    → Must run from "Developer Command Prompt for VS 2022", not regular cmd
    → Or run: "C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Auxiliary/Build/vcvars64.bat"

  Problem: "Could not find OpenSSL"
    → OPTION A: Use vcpkg (see above)
    → OPTION B: Set OPENSSL_ROOT_DIR environment variable
    → OPTION C: Install prebuilt OpenSSL and add to PATH

  Problem: "pybind11 not found"
    → Run: pip install pybind11

  Problem: Module builds but import fails
    → Windows: OpenSSL DLLs must be on PATH
    → If using vcpkg: Add C:/vcpkg/installed/x64-windows/bin to PATH
    → If using prebuilt: Add C:/Program Files/OpenSSL-Win64/bin to PATH

═══════════════════════════════════════════════════════════════════════════════
OUTPUT
═══════════════════════════════════════════════════════════════════════════════

  The compiled module (cpp_engine*.pyd on Windows, cpp_engine*.so on Linux/macOS)
  is placed directly in the project root, next to transfer.py, so that
  `import cpp_engine` works without any sys.path changes.

  Expected output file: cpp_engine.cp312-win_amd64.pyd (version number varies)
"""

import argparse
import glob
import os
import platform
import shutil
import subprocess
import sys


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR   = os.path.join(PROJECT_ROOT, "cpp_engine")
BUILD_DIR    = os.path.join(ENGINE_DIR, "build")


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(cmd, **kwargs):
    """Run a shell command, raising CalledProcessError on failure."""
    print(f"\n>>> {' '.join(str(c) for c in cmd)}\n")
    subprocess.run(cmd, check=True, **kwargs)


def find_exe(name):
    """Return the full path of *name* on PATH, or None."""
    return shutil.which(name)


def check_prerequisites():
    errors = []

    if not find_exe("cmake"):
        errors.append(
            "cmake not found on PATH.\n"
            "  → Install CMake from: https://cmake.org/download/\n"
            "  → During install, select 'Add CMake to system PATH'\n"
            "  → OR install via Visual Studio Installer: 'C++ CMake tools for Windows'"
        )

    if platform.system() == "Windows":
        # Look for cl.exe (MSVC) — must be run from a VS Developer Command Prompt
        if not find_exe("cl"):
            errors.append(
                "MSVC compiler (cl.exe) not on PATH.\n"
                "  → MUST run from 'Developer Command Prompt for VS 2022'\n"
                "  → Find it: Start Menu → Search 'Developer Command Prompt'\n"
                "  → OR run manually:\n"
                "    \"C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Auxiliary\\Build\\vcvars64.bat\"\n"
                "  → If VS not installed, download from: https://visualstudio.microsoft.com/downloads/\n"
                "    Select 'Desktop development with C++' during installation"
            )
    else:
        if not find_exe("g++") and not find_exe("clang++"):
            errors.append(
                "No C++ compiler (g++ / clang++) found on PATH.\n"
                "  Ubuntu/Debian: sudo apt install build-essential\n"
                "  Fedora/RHEL:   sudo dnf install gcc-c++\n"
                "  macOS:         xcode-select --install"
            )

    # Check for pybind11
    try:
        import pybind11
    except ImportError:
        errors.append(
            "pybind11 not installed.\n"
            "  → Run: pip install pybind11\n"
            "  → This is required for Python-C++ bindings"
        )

    if errors:
        print("\n" + "=" * 70)
        print("❌ PREREQUISITE ERRORS — Fix these before building:")
        print("=" * 70)
        for i, e in enumerate(errors, 1):
            print(f"\n{i}. {e}")
        print("\n" + "=" * 70 + "\n")
        sys.exit(1)


def find_built_module():
    """Return the path of the built .pyd / .so in the project root, or None."""
    patterns = [
        os.path.join(PROJECT_ROOT, "cpp_engine*.pyd"),
        os.path.join(PROJECT_ROOT, "cpp_engine*.so"),
        os.path.join(PROJECT_ROOT, "cpp_engine.so"),
    ]
    for pat in patterns:
        matches = glob.glob(pat)
        if matches:
            return matches[0]
    return None


def print_openssl_help():
    print("\n" + "=" * 70)
    print("OpenSSL NOT FOUND — Choose one of these options:")
    print("=" * 70)
    if platform.system() == "Windows":
        print("""
RECOMMENDED: Install via vcpkg (automatic setup)
-------------------------------------------------
  1. Open PowerShell as Administrator:
       cd C:\\
       git clone https://github.com/microsoft/vcpkg
       cd vcpkg
       .\\bootstrap-vcpkg.bat
       .\\vcpkg install openssl:x64-windows

  2. Run build with toolchain:
       python build_cpp.py --toolchain C:\\vcpkg\\scripts\\buildsystems\\vcpkg.cmake

ALTERNATIVE: Use prebuilt OpenSSL installer
--------------------------------------------
  1. Download from: https://slproweb.com/products/Win32OpenSSL.html
     → Get "Win64 OpenSSL v3.x.x" (NOT the Light version)

  2. Install to default location:
       C:\\Program Files\\OpenSSL-Win64

  3. Set environment variable and build:
       set OPENSSL_ROOT_DIR=C:\\Program Files\\OpenSSL-Win64
       python build_cpp.py

  4. Add OpenSSL to PATH (for runtime):
       Add to PATH: C:\\Program Files\\OpenSSL-Win64\\bin
""")
    elif platform.system() == "Darwin":
        print("""
macOS: Install via Homebrew
----------------------------
  brew install openssl
  export OPENSSL_ROOT_DIR=$(brew --prefix openssl)
  python3 build_cpp.py
""")
    else:
        print("""
Linux: Install via package manager
-----------------------------------
  Ubuntu/Debian:
    sudo apt install libssl-dev

  Fedora/RHEL:
    sudo dnf install openssl-devel

  Then run:
    python3 build_cpp.py
""")
    print("=" * 70 + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build the FIshare C++ engine module.")
    parser.add_argument("--debug",     action="store_true", help="Build Debug (default: Release)")
    parser.add_argument("--clean",     action="store_true", help="Remove build dir before building")
    parser.add_argument("--toolchain", default=None,        help="CMake toolchain file (e.g. vcpkg)")
    args = parser.parse_args()

    build_type = "Debug" if args.debug else "Release"

    print("\n" + "=" * 70)
    print("  🐟 FIshare C++ Engine Builder")
    print("=" * 70)
    print(f"  Project root : {PROJECT_ROOT}")
    print(f"  Engine dir   : {ENGINE_DIR}")
    print(f"  Build dir    : {BUILD_DIR}")
    print(f"  Build type   : {build_type}")
    print(f"  Python       : {sys.executable}")
    print(f"  Python ver   : {sys.version.split()[0]}")
    print("=" * 70)

    check_prerequisites()

    if args.clean and os.path.isdir(BUILD_DIR):
        print(f"\nCleaning {BUILD_DIR} ...")
        shutil.rmtree(BUILD_DIR)

    os.makedirs(BUILD_DIR, exist_ok=True)

    # ── CMake configure ───────────────────────────────
    cmake_cmd = [
        "cmake",
        ENGINE_DIR,
        f"-DCMAKE_BUILD_TYPE={build_type}",
        f"-DPython_EXECUTABLE={sys.executable}",
        f"-DPython3_EXECUTABLE={sys.executable}",
    ]
    if args.toolchain:
        cmake_cmd.append(f"-DCMAKE_TOOLCHAIN_FILE={args.toolchain}")

    # Pass along OPENSSL_ROOT_DIR from environment if set
    openssl_root = os.environ.get("OPENSSL_ROOT_DIR")
    if openssl_root:
        cmake_cmd.append(f"-DOPENSSL_ROOT_DIR={openssl_root}")

    try:
        run(cmake_cmd, cwd=BUILD_DIR)
    except subprocess.CalledProcessError:
        # Likely OpenSSL not found — give actionable guidance
        print_openssl_help()
        sys.exit(1)

    # ── CMake build ───────────────────────────────────
    build_cmd = [
        "cmake", "--build", BUILD_DIR,
        "--config", build_type,
        "--parallel",
    ]
    try:
        run(build_cmd)
    except subprocess.CalledProcessError:
        print("\nBuild failed. Review the compiler output above.")
        sys.exit(1)

    # ── Verify output ─────────────────────────────────
    module_path = find_built_module()
    if module_path:
        print("\n" + "=" * 70)
        print("  ✅ BUILD SUCCESSFUL!")
        print("=" * 70)
        print(f"  Module built: {os.path.basename(module_path)}")
        print(f"  Location:     {module_path}")
        print("=" * 70)
        print("\n🔍 Verifying import...\n")
        result = subprocess.run(
            [sys.executable, "-c",
             "import cpp_engine; print('  ✓ cpp_engine imported successfully'); "
             "print(f'  ✓ Version: {cpp_engine.__version__}'); "
             "print(f'  ✓ Available: {cpp_engine.available}')"],
            cwd=PROJECT_ROOT
        )
        if result.returncode == 0:
            print("\n" + "=" * 70)
            print("  🚀 READY TO USE!")
            print("=" * 70)
            print("  Next steps:")
            print("    1. Run: python app.py")
            print("    2. Enjoy 2-3x faster file transfers!")
            print("=" * 70 + "\n")
        else:
            print("\n⚠️  WARNING: Module built but import failed.")
            print("   Check that OpenSSL DLLs are on PATH:")
            if platform.system() == "Windows":
                print("   → If using vcpkg: Add C:\\vcpkg\\installed\\x64-windows\\bin to PATH")
                print("   → If using prebuilt: Add C:\\Program Files\\OpenSSL-Win64\\bin to PATH")
            else:
                print("   → Check LD_LIBRARY_PATH includes OpenSSL lib directory")
            print()
    else:
        print("\n⚠️  WARNING: Build succeeded but module not found in project root.")
        print("Check CMake output above for the actual output location.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
