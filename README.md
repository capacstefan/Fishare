# P2P LAN Share

Zero-configuration peer-to-peer file, text and folder sharing on a local
network. No server, no account, no Internet — peers discover each other via
mDNS and transfer over TLS at full LAN speed. Runs on **Windows and Linux**.

## Features

- **Auto-discovery** of peers on the LAN (mDNS / Zeroconf).
- **File transfer** over TLS 1.3 with streaming SHA-256 integrity checks.
- **Quick text** messaging between peers.
- **One-way folder sync** with live mirroring (watchdog).
- **QR phone bridge**: a phone in the same Wi-Fi uploads files / text from a
  browser, no app install.
- **Security**: self-signed TLS, fingerprint-based identity, optional PIN,
  per-peer mute, path-traversal protection.

## Requirements

- **Python 3.11+**
- A **C++ compiler** (only needed to build the native SHA-256 library):
  - Windows: MSVC (Visual Studio Build Tools, "Desktop development with
    C++") or MinGW-w64 `g++`.
  - Linux: `g++` or `clang++` (`sudo apt install build-essential`).

## Quick start (run from source)

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Build the native library (once)
python native/build.py

# 4. Run the app
python -m fishare.main
```

Step 3 compiles `p2p_native.dll` (Windows) or `libp2p_native.so` (Linux) and
copies it into the package. The app will not start without it.

## Building the native module

The C++ SHA-256 library is built separately so it can be reused both from
source and inside the packaged executable:

```bash
python native/build.py
```

It auto-selects a compiler per OS (MSVC → MinGW on Windows; g++ → clang++ on
Linux) and copies the result into `fishare/`.

## Building a standalone executable

```bash
python build.py
```

Produces a single self-contained file (no installer, no Python required on
the target machine):

- Windows → `dist/P2P LAN Share.exe`
- Linux → `dist/P2P LAN Share`

`build.py` compiles the native library first if it is missing, then bundles
everything with PyInstaller.

## Testing

```bash
python -m pytest                      # full suite (87 tests)
python -m pytest -m integration       # only end-to-end TLS transfers
python -m pytest tests/test_native.py # a single module
```

**Expected result:** all tests pass. The suite is isolated — it redirects
app data to a temp folder and uses in-process sockets / Flask's test client,
so it never touches your real settings or binds public ports.

> Note: two filesystem/timing-sensitive tests (`test_storage` threaded
> append, one `test_sync` round-trip) can occasionally flake on Windows when
> antivirus or the search indexer locks a file mid-write. They pass on a
> re-run or in isolation — these are environmental, not logic, failures.

### What the tests cover

| Test file              | Tests | Verifies                                                        |
| ---------------------- | ----- | --------------------------------------------------------------- |
| `test_native.py`       | 5     | C++ SHA-256 matches `hashlib` bit-for-bit; skips if not built.  |
| `test_protocol.py`     | 11    | Wire framing, size limits, concurrent writers, TLS contexts.    |
| `test_network.py`      | 13    | End-to-end TLS transfer integrity, cancel, offline/mute/reject. |
| `test_sync.py`         | 7     | Path-traversal guard, initial scan, delete events.              |
| `test_web_server.py`   | 10    | QR routes, token gate, uploads, unique names, text limit.       |
| `test_crypto_utils.py` | 4     | Self-signed cert generation, idempotency, RSA ≥ 2048.           |
| `test_discovery.py`    | 9     | Peer display, stable fingerprint id, mute toggle.               |
| `test_storage.py`      | 14    | Atomic + thread-safe JSON persistence, corrupt-file fallback.   |
| `test_util.py`         | 14    | Size/ETA formatting, unique paths, local IP.                    |

## Project structure

```
build.py              # PyInstaller build (builds native lib first if missing)
native/
  p2p_native.cpp      # C++ SHA-256 (FIPS 180-4), cross-platform C ABI
  build.py            # compiles the native lib per OS
fishare/        # application package
  main.py             # entry point
  config.py util.py   # constants, helpers
  crypto_utils.py protocol.py discovery.py
  network.py sync.py web_server.py storage.py
  native.py           # ctypes bridge to the compiled library
  gui/                # PyQt6 widgets, tabs, dialogs, theme
tests/                # pytest suite (mirrors the modules)
docs/                 # thesis reference document
requirements.txt pytest.ini
```

## Networking & data

- **Ports:** TCP 51821 (transfer), TCP 51822 (QR web), UDP 5353 (mDNS).
  Allow these through the firewall (Windows prompts on first run).
- **User data:** `%APPDATA%\fishare\` (Windows) or `~/fishare/`
  (Linux) — holds the TLS cert/key, settings, history, quick-texts and mute
  list.
