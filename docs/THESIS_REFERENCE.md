# P2P LAN Share — Thesis Reference Document

> Purpose: Single source of truth for writing a bachelor-degree paper about
> the application. It captures **what the app does**, **why it is built that
> way**, **what is genuinely better than the alternatives**, **the concepts
> and patterns respected**, and **the data needed to draw the standard UML
> diagrams** (use case, class, component, deployment, sequence, activity,
> state). An agent reading this file should be able to write the entire
> document without re-exploring the source code.

---

## 1. Executive Summary

**P2P LAN Share** is a cross-platform (Windows and Linux) desktop
application for _zero-configuration_ peer-to-peer file, text and folder
sharing inside a local network. It
replaces ad-hoc workflows such as e-mailing files to yourself, plugging in
USB sticks, uploading to a cloud just to download two metres away, or
running heavy collaboration suites for a 5 MB hand-off.

- **Topology:** strict peer-to-peer. No server, no account, no Internet.
- **Discovery:** mDNS / DNS-SD (Bonjour-compatible) — peers see each other
  the moment both launch the app on the same network.
- **Transport:** TLS 1.2+ over TCP with a self-signed certificate generated
  on first run; identity is the SHA-256 fingerprint of that certificate.
- **Integrity:** every file is hashed with streaming SHA-256 (custom C++
  module, GIL-released) and verified after transfer.
- **Phone bridge:** an on-demand QR-code web server (HTTPS, self-signed)
  lets any phone in the same Wi-Fi upload files / send text without
  installing anything.
- **Folder sync:** one-way watchdog-driven live mirroring.
- **Portability:** a single codebase runs on Windows and Linux; the only
  native artefact is a small C++ library compiled per operating system.
- **Stack:** Python 3.11, PyQt6, Zeroconf, cryptography, Flask, watchdog,
  qrcode + a custom C++ native library for streaming SHA-256
  (`p2p_native.dll` on Windows, `libp2p_native.so` on Linux).

---

## 2. Functional Feature Inventory

Use this list as the master checklist of "what the application can do".

### 2.1 Identity & Discovery

1. Automatic mDNS advertisement (`_p2planshare._tcp.local.`).
2. Stable per-device identifier = SHA-256 fingerprint (first 16 hex) of the
   local TLS certificate — survives rename and IP change.
3. Live list of peers with online/offline indicator (🟢 / 🔴).
4. **Impersonation detection**: if a peer appears with a name already in use
   but a different fingerprint, the app surfaces a visible warning.
5. Heartbeat republish every 20 s; ghost peers disappear via mDNS TTL.
6. Manual **Online / Offline** toggle — offline peers refuse all incoming
   offers and are filtered out of the recipient list.
7. **Editable display name** with one-click reset to hostname.

### 2.2 File Transfer

1. Multi-file batch send, multi-recipient fan-out from a single Send click.
2. Cumulative **batch progress bar** (0 → 100 across the whole batch,
   smoothly throttled to ~10 Hz).
3. **Per-row cancel button** (`✕`) — works for in-progress, queued and
   pre-connect states; idempotent and race-safe.
4. **Receiver consent dialog** for every incoming offer.
5. Optional **PIN lock**: sender shows a 4-digit PIN, receiver must type it
   to accept (defence against social-engineering / wrong-recipient).
6. **Size limit**: 50 GB per file/total (configurable).
7. **Streaming SHA-256** integrity check at the end of each file; corrupt
   parts are deleted, never renamed to a real filename.
8. **Atomic write**: data lands in `<name>.part`, only renamed to the final
   name once the hash matches.
9. **Filename collision handling** via `unique_path()` — `report (1).pdf`,
   `report (2).pdf`, …
10. **Concurrent outbound cap** (`MAX_CONCURRENT_TRANSFERS = 3`) via a
    semaphore; extra tasks show as "queued".
11. **Inbound concurrency unbounded** by design (servers shouldn't throttle
    requesters; receivers run one thread per accepted socket).
12. **Mute** a peer by fingerprint — muted senders are silently rejected on
    the wire ("muted" reason).
13. Status feedback: `waiting_accept → sending → done` (or `rejected /
failed / cancelled / offline`).

### 2.3 Quick Text (mini-chat)

1. Send up to 500 chars to one or several peers.
2. Receiver inbox tab with sender/date.
3. Counter, accept dialog, history entry — same primitives as file flow.

### 2.4 One-Way Folder Sync

1. Pick a folder + one peer, click **Start Sync**.
2. Initial bulk push of existing files.
3. Live mirroring via `watchdog`: create, modify, rename, delete.
4. Path traversal protection on the receiver (every relative path resolved
   and re-checked against the destination root).
5. Either side can cancel ("Cancel Sync") — peer is notified via a
   `sync_event op=stop` frame, the socket is closed cleanly.

### 2.5 QR Web Server (phone bridge)

1. Generates a QR code containing `https://<lan-ip>:51822/<token>/`
   (self-signed HTTPS; a browser warning is expected).
2. The token is a 16-byte `secrets.token_urlsafe` — URLs cannot be guessed.
3. Token check uses `hmac.compare_digest` (timing-safe).
4. Mobile-first responsive HTML (Apple-inspired styling).
5. Upload multiple files at once; up to 500-char quick text.
   Total upload cap is 4 GB; max 20 files per request.
6. Uploads land in the same download folder; history entry is created.
7. Server runs in a background thread; **Stop** shuts it down cleanly.

### 2.6 History & Settings

1. Persistent **transfer history** (date, direction, peer, type, count, size).
2. Persistent **quick-text inbox**.
3. Persistent **muted list** (by fingerprint, not by name → rename-proof).
4. Persistent **settings** (device name, online state, default download dir).
5. All persistence is **atomic** (`*.tmp` + `Path.replace`) and **thread-
   safe** (reentrant lock).
6. **Clear history** button.

### 2.7 Cross-Cutting UX

1. Single window, four tabs: File Transfer · Quick Text · Tools · History.
2. Apple-inspired light theme via a single `STYLESHEET` constant.
3. Custom `ToggleSwitch` widget with property-animated thumb.
4. Status bar always shows current identity + online state + download dir.
5. Long-running operations never block the UI — every transfer runs in its
   own thread; results are marshalled to the UI via Qt signals.

---

## 3. Why this app is genuinely better than the alternatives

Comparison axis used in the thesis (one paragraph per row is enough).

| Concern                     | Cloud (Drive, Dropbox, WeTransfer)      | Phone tools (AirDrop, Nearby Share)          | Enterprise (SharePoint, OneDrive) | **P2P LAN Share**                                                      |
| --------------------------- | --------------------------------------- | -------------------------------------------- | --------------------------------- | ---------------------------------------------------------------------- |
| **Privacy**                 | Files traverse third-party servers.     | Vendor-locked (Apple↔Apple / Google↔Google). | Files stored on org cloud.        | Bytes never leave the LAN.                                             |
| **Internet required**       | Yes — slow uploads even for LAN peers.  | No, but vendor-locked.                       | Yes.                              | **No** — works on an air-gapped LAN.                                   |
| **Cross-OS / cross-vendor** | Yes.                                    | No.                                          | Yes, with account.                | Windows/Linux + any phone with a browser; the protocol is OS-agnostic. |
| **Speed**                   | Capped by WAN uplink.                   | LAN.                                         | WAN.                              | **Full LAN throughput** (chunked TCP + GIL-released native hash).      |
| **Setup**                   | Account, login, sometimes an installer. | Same vendor.                                 | IT-provisioned.                   | **Zero**: launch the exe and the peer is visible.                      |
| **Phone interop**           | Phone app required.                     | Same-vendor only.                            | Phone app required.               | **QR code** — any browser, no install.                                 |
| **Security**                | TLS to vendor, vendor-readable.         | E2E within vendor.                           | Org-controlled.                   | TLS between peers, integrity hash, PIN, mute, impersonation detection. |
| **Cost / telemetry**        | Free tier + ads / quotas.               | Free.                                        | Paid.                             | **Free, no telemetry, no account, no Internet call-home.**             |
| **Discoverability**         | N/A.                                    | Bluetooth + Wi-Fi Direct.                    | N/A.                              | mDNS — same way printers and Chromecasts are found.                    |
| **Footprint**               | Cloud quota.                            | Native OS feature.                           | Cloud quota.                      | One 68 MB exe; no install.                                             |

**One-line pitch for the abstract:** _"AirDrop for any LAN, between any
devices, with zero configuration and full local-network speed."_

---

## 4. User-Friendliness Highlights

Items the thesis can quote as concrete UX decisions.

1. **No login screen, no onboarding** — the app is usable in <5 s.
2. **Always-visible identity & state** in the status bar.
3. **One-click reset** for the device name.
4. **Toggle switch** (not a checkbox) for Online/Offline — physical metaphor.
5. **Drag-and-drop file list** with double-click to remove.
6. **PIN labelling** appears inline once enabled.
7. **Live multi-row progress** segregated by direction (↑ up / ↓ down) and
   peer — never collapses two simultaneous transfers into one bar.
8. **Cancel "✕"** sits where a user naturally looks: end of the bar.
9. **Auto-clear** of finished rows after 4 s — the tray stays uncluttered.
10. **Receiver dialog** describes the offer plainly: _"Wants to send you 12
    files (Total: 84.3 MB)."_
11. **QR popup** is large, centred and accompanied by the human-readable URL.
12. **Mobile web page** uses the same colour palette as the desktop client
    so users feel they are talking to the same app.
13. **Error messages are human-language** ("Peer offline", "PIN mismatch",
    "too large", "cancelled") — never raw exceptions.
14. **Mute is by fingerprint**, not by name → renaming a muted peer does
    not silently un-mute them.

---

## 5. Engineering Concepts Respected (theory chapter)

These map onto the typical bachelor-thesis "theoretical background" section.

### 5.1 Software-engineering principles

- **Single Responsibility / module cohesion**: each file owns exactly one
  concern (`discovery.py`, `protocol.py`, `network.py`, `sync.py`,
  `web_server.py`, `storage.py`, `gui/*`).
- **Separation of concerns**: GUI never touches sockets; networking layer
  never touches Qt widgets. The contract between them is **Qt signals**.
- **Layered architecture**: Presentation (gui/) → Application (network,
  sync, web_server) → Domain protocol (protocol.py, discovery.py) →
  Infrastructure (storage.py, native.py, crypto_utils.py).
- **Dependency Inversion**: `TransferServer` receives a `get_state`
  callable, not the `MainWindow`, so it can be tested in isolation.
- **DRY** in the wire layer: every frame goes through `Wire.send_json /
send_data / recv_frame`.
- **Defensive programming at boundaries only** — internal helpers trust
  their callers; validation lives on the wire and on disk paths.

### 5.2 Patterns

- **Observer / Pub-Sub** via Qt signals (`pyqtSignal`).
- **Producer / Consumer with bounded concurrency** via a Semaphore
  (`TransferQueue`).
- **Strategy** in `TransferTask` — same envelope for `files`, `text`,
  `sync` kinds.
- **Façade** in `Wire` — hides framed reads/writes behind one object.
- **Template Method** in `_recv_files` / `_send_files` — fixed sequence
  (`file_begin → N×data → file_end → all_done`).
- **Observer** in `watchdog` for filesystem events.
- **Active Object**: each `TransferTask` runs on its own thread and
  publishes results via signals.

### 5.3 Concurrency model

- **Cooperative cancellation** via `threading.Event` checked at every loop
  boundary; the socket is closed to interrupt blocking I/O.
- **Mutex-protected sends** (`Wire._send_lock`) — header + payload must be
  atomic even under multiple writer threads.
- **Reentrant lock** in `storage.py` for JSON I/O.
- **GIL release** — `ctypes.CDLL` releases the GIL on every native call;
  SHA-256 runs in parallel with Python network I/O.
- **No shared mutable UI state from worker threads** — workers only emit
  signals; Qt routes them to the GUI thread.

### 5.4 Security & networking

- **Self-signed TLS** generated locally with cryptography library
  (RSA-2048, SHA-256, SAN, 10-year validity).
- **Fingerprint identity** instead of trust-on-first-use names —
  rename-proof, MITM-detectable.
- **Integrity hash** (SHA-256) per file, end-to-end.
- **Path traversal protection** in `sync.py::_safe()`.
- **Timing-safe token compare** in `web_server.py` (`hmac.compare_digest`).
- **QR HTTPS** with a self-signed certificate (browser warning unless trusted).
- **QR upload limits**: 4 GB total and max 20 files per request.
- **Sandbox by filename**: every received file is written as `*.part`
  first, only renamed on hash success — half-files never appear.
- **No outbound Internet calls**: app is fully usable offline.

### 5.5 Protocol design

- **Framed protocol**: `[1B type][4B BE length][payload]` — language-
  agnostic, replayable from a packet capture.
- **JSON for control, binary for data** — best of both worlds.
- **Stateful handshake**: `offer → response → body → all_done`.
- **Backpressure** is TCP's, not application's — simple and correct.

### 5.6 UX / HCI principles

- **Fitts's Law**: cancel button placed _at the end_ of the progress bar
  (where the eye lands as it tracks progress).
- **Hick's Law**: only four tabs.
- **Visibility of system state** (Nielsen #1): status bar + per-row state.
- **Match between system and real world** (Nielsen #2): "Online", "Send",
  "Cancel" — no jargon.
- **Recognition rather than recall**: peers are listed with their device
  name, not their IP.
- **Error prevention**: PIN, accept dialog, fingerprint warning.

### 5.7 The native C++ module — rationale, internals & integration

The application embeds one hand-written native component:
`native/p2p_native.cpp`, a dependency-free implementation of **SHA-256**
(FIPS 180-4) exposed through a small, stable **C ABI**. It is compiled into
a shared library (`p2p_native.dll` on Windows, `libp2p_native.so` on Linux)
and loaded at runtime by `p2p_lan_share/native.py` through Python's
`ctypes`. SHA-256 is the integrity primitive for every transfer, so this
module sits squarely in the file-transfer hot loop.

**Why a native module (thesis justification).**

- **Language-interoperability showcase.** It demonstrates a clean
  Python ↔ C/C++ boundary using `ctypes` and an opaque handle (`void*`),
  i.e. a stable C ABI rather than a heavyweight binding generator — no
  pybind11/Cython is required at call time. (`pybind11` is listed only as
  an optional convenience.)
- **Streaming (incremental) hashing.** The hasher is created once
  (`p2p_sha256_new`), fed arbitrary chunks (`p2p_sha256_update`) and
  finalised (`p2p_sha256_final`). This mirrors the transfer loop, which
  hashes each 1 MiB chunk as it is read from / written to the socket, so a
  50 GB file is verified incrementally without ever holding more than one
  chunk in memory.
- **GIL released during hashing.** `ctypes.CDLL` releases CPython's Global
  Interpreter Lock for the duration of each native call. While one thread
  crunches SHA-256 in C++, other Python threads keep doing socket I/O, so
  concurrent transfers overlap CPU-bound hashing with I/O-bound networking.
- **Zero dependencies, tiny footprint.** The implementation uses no STL and
  no third-party crypto, so the resulting library is a few kilobytes, has
  no transitive dependencies to bundle, and is easy to audit.
- **Deterministic, standards-compliant correctness.** Because it is a
  textbook FIPS 180-4 implementation, its output is verified **bit-for-bit
  against Python's `hashlib`** in `tests/test_native.py`.

**Public C ABI (the entire surface).**

| Symbol               | Signature                              | Purpose                                  |
| -------------------- | -------------------------------------- | ---------------------------------------- |
| `p2p_sha256_new`     | `void* (void)`                         | Allocate + init a hasher; opaque handle. |
| `p2p_sha256_update`  | `void (void*, const uint8_t*, size_t)` | Feed one chunk of data.                  |
| `p2p_sha256_final`   | `void (void*, uint8_t[32])`            | Write the 32-byte digest.                |
| `p2p_sha256_free`    | `void (void*)`                         | Release the hasher.                      |
| `p2p_native_version` | `uint32_t (void)`                      | Load-probe / version (`0x00010000`).     |

**Cross-platform compilation.** A single preprocessor macro selects the
export attribute, so the _same_ source compiles on both toolchains:

```cpp
#if defined(_WIN32)
#  define P2P_API extern "C" __declspec(dllexport)
#else
#  define P2P_API extern "C" __attribute__((visibility("default")))
#endif
```

`native/build.py` chooses a compiler per OS — MSVC (`cl /LD /O2`) with a
MinGW `g++` fallback on Windows, and `g++` / `clang++`
(`-shared -fPIC -O2`) on Linux — then copies the artefact into the package
directory so that both `python -m p2p_lan_share.main` and the PyInstaller
bundle can find it.

**The Python bridge (`native.py`).** `_load()` searches, in order, the
package directory, then the sibling `native/` folder, then the bare library
name, raising an actionable "build it first" `RuntimeError` if nothing
loads. `argtypes` / `restype` are declared so `ctypes` marshals arguments
safely; the `_NativeSha` wrapper offers `update()` / `hexdigest()` and
guarantees the C handle is released — in `hexdigest()` and again via a
`__del__` safety net — preventing both memory leaks and double-frees. A
finalised hasher reused by mistake raises `RuntimeError` instead of
corrupting memory.

**Graceful degradation.** The library is a build artefact (git-ignored); if
it is missing, `native.py` fails fast with a clear message, and the native
test module **skips** rather than fails, keeping a fresh checkout green
until `python native/build.py` has run.

---

## 6. Cases the application handles gracefully (robustness chapter)

| #   | Edge case                                          | How it is handled                                                                                                                                |
| --- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | Cancel clicked exactly when the transfer finishes  | `cancel()` is idempotent; row checks `_finished`; queue checks the Event twice (pre- and post-semaphore).                                        |
| 2   | Cancel of a queued (not yet started) task          | `TransferQueue._run` checks `task.cancelled` before & after acquiring the semaphore.                                                             |
| 3   | Cancel mid-stream                                  | Sender raises `TransferCancelled` at the next loop boundary; receiver gets a closed socket and emits `recv_cancelled` rather than `recv_failed`. |
| 4   | Peer disappears mid-transfer                       | Socket error → status "failed" with the underlying reason; row auto-clears.                                                                      |
| 5   | File grows or shrinks during send                  | Read is clamped: `f.read(min(_CHUNK, spec.size - file_done))`; short read raises `IOError`.                                                      |
| 6   | Corrupted bytes on the wire                        | SHA-256 mismatch → `.part` is deleted, never renamed to a real filename.                                                                         |
| 7   | Filename collision in download folder              | `unique_path()` appends ` (N)` until free.                                                                                                       |
| 8   | Path-traversal in folder-sync (`../../etc/passwd`) | `_safe()` resolves and verifies the result is inside the destination root.                                                                       |
| 9   | Two peers share the same display name              | mDNS service name embeds the fingerprint; UI shows an impersonation warning.                                                                     |
| 10  | Device renamed                                     | `peer_id` (cert fingerprint) is stable → mute list still applies; history keeps both names.                                                      |
| 11  | App offline                                        | `_handle()` rejects offers with reason `"offline"`; sender's row shows "offline".                                                                |
| 12  | QR uploads > 4 GB or too many files                | Flask `MAX_CONTENT_LENGTH` returns HTTP 413; file-count limit returns a UI error.                                                                |
| 13  | Concurrent multi-recipient send                    | One task per recipient; each has its own row; semaphore caps to 3 simultaneous.                                                                  |
| 14  | Crash recovery                                     | Settings, history, muted list are stored via atomic `*.tmp + replace`.                                                                           |
| 15  | Storage JSON corrupted                             | `_read` returns the default and the app keeps running.                                                                                           |
| 16  | Sync sender folder doesn't exist                   | `_run` raises; `finished("error: …")` is emitted; UI shows the reason.                                                                           |
| 17  | Sync delete races with create                      | watchdog's `on_moved` emits delete+put atomically.                                                                                               |
| 18  | TLS handshake fails                                | Connection is logged and dropped; main accept loop keeps running.                                                                                |
| 19  | mDNS update fails                                  | `_republish()` falls back to unregister+register.                                                                                                |
| 20  | QR token guessing                                  | URL contains a 22-char random token; constant-time compare; 404 otherwise.                                                                       |

---

## 7. Architecture at a glance (for the architecture chapter)

### 7.1 Modules

| File                    | Responsibility                                                                                     |
| ----------------------- | -------------------------------------------------------------------------------------------------- |
| `main.py`               | Entry point — creates QApplication, applies theme, shows MainWindow.                               |
| `config.py`             | Constants (ports, chunk size, paths, app name).                                                    |
| `util.py`               | Tiny pure helpers (`fmt_size`, `fmt_eta`, `unique_path`, `local_ip`).                              |
| `crypto_utils.py`       | One-time generation of self-signed TLS cert + key.                                                 |
| `protocol.py`           | `Wire` framed reader/writer + TLS contexts + `open_offer` handshake helper.                        |
| `discovery.py`          | `PeerRegistry` — mDNS advertise + browse, fingerprint-based identity, mute, heartbeat.             |
| `network.py`            | `TransferServer` (accept loop, receiver), `TransferTask` (sender), `TransferQueue` (semaphore).    |
| `sync.py`               | `SyncSender` (initial scan + watchdog), `SyncReceiver` (event consumer, path-traversal guard).     |
| `web_server.py`         | `QrWebServer` — Flask app + QR code generator.                                                     |
| `storage.py`            | Atomic, thread-safe JSON persistence for settings, history, inbox, mute.                           |
| `native.py`             | `ctypes` bridge to the native library (`p2p_native.dll` / `libp2p_native.so`) — streaming SHA-256. |
| `native/p2p_native.cpp` | Pure-C++ FIPS-180-4 SHA-256, exposed via C ABI.                                                    |
| `gui/main_window.py`    | Composes services, routes signals, owns life-cycle.                                                |
| `gui/tab_transfer.py`   | File-transfer tab (peer selector, file list, PIN, send, progress rows).                            |
| `gui/tab_quicktext.py`  | Quick-text composer + inbox.                                                                       |
| `gui/tab_tools.py`      | Folder sync + QR web server controls.                                                              |
| `gui/tab_history.py`    | Persistent table view of past transfers.                                                           |
| `gui/dialogs.py`        | Accept-offer, quick-text editor, quick-text reader.                                                |
| `gui/peer_list.py`      | Reusable peer list widget.                                                                         |
| `gui/_widgets.py`       | Generic atoms (`PeerSelector`, headings).                                                          |
| `gui/theme.py`          | Light theme stylesheet + `ToggleSwitch`.                                                           |
| `native/build.py`       | Cross-platform native build (Windows `.dll` / Linux `.so`); copies the lib into the package.       |
| `build.py`              | PyInstaller build (Windows `.exe` / Linux binary); builds the native lib first if it is missing.   |

### 7.2 Layers (top-down)

1. **Presentation** — PyQt6 widgets, dialogs, theme.
2. **Application orchestration** — `MainWindow` wires services together.
3. **Services** — `TransferServer/Task/Queue`, `SyncSender/Receiver`,
   `QrWebServer`, `PeerRegistry`.
4. **Protocol** — `Wire`, frame types, JSON envelopes, TLS contexts.
5. **Infrastructure** — `storage`, `crypto_utils`, `native`, OS sockets.

### 7.3 Repository layout (after the cross-platform refactor)

```
ppp/                          # project root
├─ build.py                   # PyInstaller build (Win .exe / Linux binary);
│                             #   compiles the native lib first if it is missing
├─ native/                    # C++ module — beside the package, not inside it
│   ├─ p2p_native.cpp         #   FIPS 180-4 SHA-256, C ABI, cross-platform source
│   └─ build.py               #   compiles -> p2p_native.dll / libp2p_native.so
├─ p2p_lan_share/             # the importable application package
│   ├─ main.py  config.py  util.py
│   ├─ crypto_utils.py  protocol.py  discovery.py
│   ├─ network.py  sync.py  web_server.py  storage.py
│   ├─ native.py              #   ctypes bridge that loads the compiled library
│   └─ gui/                   #   PyQt6 widgets, tabs, dialogs, theme
├─ tests/                     # pytest suite mirroring the modules one-to-one
├─ docs/
│   └─ THESIS_REFERENCE.md    # this document
└─ requirements.txt  pytest.ini  .gitignore
```

**Design notes.**

- The C++ sources live in a top-level `native/` folder — _beside_ the
  package — so the runtime module `native.py` and the native source folder
  never collide, and the build tooling is easy to find.
- The compiled library is **copied into** `p2p_lan_share/` at build time
  (and git-ignored) so `native.py` finds it next to itself in every run
  mode: from source, via `python -m`, or inside the frozen executable.
- Two entry points, one per concern: `native/build.py` builds the C++
  library; the root `build.py` packages the whole app (and invokes the
  former automatically when the library is absent).
- Documentation lives in `docs/` and is never shipped inside the
  importable package.

---

## 8. Diagram-Building Material

Each subsection below contains the **actors, entities, messages and order**
needed to draw the diagram. Keep diagrams simple — pick at most 6-8
boxes/lifelines each.

### 8.1 Use Case Diagram

**Actors**

- **User (sender)** — the person initiating an action on PC A.
- **User (receiver)** — the person on PC B who accepts/rejects.
- **Phone User** — anyone who scans the QR code.
- **Peer App** — the same application running on another PC (system actor).
- **Operating System** — provides mDNS, file system, network.

**Use cases (verb + noun, group in packages)**

- _Discover peers_ (auto, includes `Advertise self`, `Watch network`).
- _Manage identity_: Set device name · Toggle online/offline.
- _Send files_ (includes `Select files`, `Select peers`, optional
  `Enable PIN`).
- _Receive files_ (includes `Approve offer`, optional `Enter PIN`,
  extends `Detect impersonation`).
- _Cancel transfer_ (extends `Send files` and `Receive files`).
- _Send quick text_ / _Receive quick text_.
- _Start folder sync_ / _Stop folder sync_.
- _Start QR server_ / _Stop QR server_.
- _Mute peer_ / _Unmute peer_.
- _View history_ / _Clear history_.
- _Phone uploads file via QR_ (Phone User actor → system).

**Notable relationships**

- `Receive files` _extends_ `Detect impersonation`.
- `Send files` _includes_ `Discover peers`.
- `Cancel transfer` is an _«extend»_ of both send and receive.

### 8.2 Class / Component Diagram

Pick these as boxes (one per file usually). Show only public collaborators.

```
+-----------------+      +-------------------+      +------------------+
|   MainWindow    |----->|   PeerRegistry    |----->|     Zeroconf     |
| (gui)           |      | (discovery.py)    |      |  (mDNS / DNS-SD) |
+--------+--------+      +-------------------+      +------------------+
         |
         |----------------------+------------------------+
         v                      v                        v
+-----------------+   +-------------------+   +---------------------+
| TransferServer  |   |  TransferQueue    |   |   QrWebServer       |
| (network.py)    |   |  (network.py)     |   |   (web_server.py)   |
+--------+--------+   +---------+---------+   +----------+----------+
         |                      |                        |
         |                      v                        v
         |              +-----------------+      +-----------------+
         |              |  TransferTask   |      |   Flask app     |
         |              | (network.py)    |      | + token / QR    |
         |              +--------+--------+      +-----------------+
         |                       |
         |                       v
         |              +-----------------+
         +------------->|     Wire        |<------- protocol.py
                        |  (framed TLS)   |
                        +--------+--------+
                                 |
                                 v
                       +---------------------+
                       |   p2p_native.dll    |
                       | (streaming SHA-256) |
                       +---------------------+

Sync feature (peer-to-peer):
   SyncSender --[watchdog FS events]--> Wire --[TLS]--> SyncReceiver
```

Important multiplicities:

- MainWindow `1 → *` TransferTask (live tasks list).
- TransferServer `1 → *` inbound threads (one per accepted socket).
- PeerRegistry `1 → *` Peer.

### 8.3 Deployment Diagram

Three nodes, one network.

```
   +-------------------------+    LAN / Wi-Fi    +-------------------------+
   |  PC A  (Windows 64-bit) |<================>|  PC B  (Windows 64-bit) |
   |  P2P LAN Share.exe      |                  |  P2P LAN Share.exe      |
   |   - PyQt6 GUI           |  TCP 51821 (TLS) |   - PyQt6 GUI           |
   |   - mDNS service        |  UDP 5353 (mDNS) |   - mDNS service        |
   |   - p2p_native.dll      |                  |   - p2p_native.dll      |
   +-------------------------+                  +-------------------------+
                ^
                |  HTTPS 51822 (token URL, same LAN)
                v
        +---------------+
        |   Phone       |
        |  (any browser)|
        +---------------+
```

Persistent files (per node, under `%APPDATA%\p2p_lan_share\` on Windows or
`~/p2p_lan_share/` on Linux): `cert.pem`, `key.pem`, `settings.json`,
`history.json`, `quicktexts.json`, `muted.json`.

### 8.4 Component Diagram (logical)

Show four logical components and their interfaces:

- **UI Component** (`gui/*`) — provides `IUserActions`, consumes
  `IServiceEvents`.
- **Discovery Component** (`discovery.py`) — provides `IPeerFeed`.
- **Transfer Component** (`network.py`, `protocol.py`) — provides
  `ITransfer`, `IServer`.
- **Auxiliary Component** (`sync.py`, `web_server.py`) — provides
  `IFolderSync`, `IQrBridge`.
- **Infrastructure** (`storage.py`, `crypto_utils.py`, `native.py`) — used
  by everyone.

Interfaces are implemented as **Qt signals** on the producer side and
**slot methods** on the consumer side (`MainWindow`).

### 8.5 Sequence Diagrams (pick 2-3 for the doc)

> Style hint: lifelines top-to-bottom — actors first, then GUI, then
> services, then network.

#### 8.5.1 Successful file transfer (the canonical happy path)

Lifelines: `User-A`, `MainWindow-A`, `TransferTask`, `Wire-A` ⇋ `Wire-B`,
`TransferServer-B`, `MainWindow-B`, `User-B`.

```
User-A      MainWindow-A   TransferTask        Wire-A==Wire-B      TransferServer-B   MainWindow-B   User-B
  |  click Send   |              |                   |                    |                  |             |
  |-------------->|  new task    |                   |                    |                  |             |
  |               |------------->|  connect (TLS)    |                    |                  |             |
  |               |              |------------------>|--- handshake ----->|                  |             |
  |               |              |  send_json offer  |                    |                  |             |
  |               |              |------------------>|------------------->| offer_received   |             |
  |               |              |                   |                    |----------------->| dialog      |
  |               |              |                   |                    |                  |<- Accept ---|
  |               |              |                   |                    |  respond(ok)     |             |
  |               |              |  recv response    |<-------------------|<-----------------|             |
  |               |              |  send_json file_begin                  |                  |             |
  |               |              |------------------>|------------------->| _recv_files      |             |
  |               |              |  send_data * N  (chunks)               | write .part      |             |
  |               |  progress----|------------------>|------------------->| file_progress--->| update bar  |
  |               |              |  send_json file_end (sha256)           | verify hash      |             |
  |               |              |  ... loop per file ...                 | rename .part     |             |
  |               |              |  send_json all_done                    |                  |             |
  |               |              |                   |------------------->| transfer_done -->| notify      |
  |               | finished(ok) |                   |                    |                  |             |
  |<--- "Sent" ---|              |                   |                    |                  |             |
```

Annotations to add as notes:

- "Every send/recv is one frame: [type | length | payload]."
- "SHA-256 hashes the stream chunk by chunk in C++ (no GIL)."
- "Wire.send is mutex-protected → header + body atomic."

#### 8.5.2 Cancel during multi-file send

```
User-A  ProgressRow  TransferTab  MainWindow  TransferTask     Wire     Server-B
  | click ✕    |          |           |             |            |          |
  |----------->| cancel_clicked       |             |            |          |
  |            |--------->|cancel_req |             |            |          |
  |            |          |---------->|_on_cancel   |            |          |
  |            |          |           |------------>| cancel()   |          |
  |            |          |           |             |  set Event |          |
  |            |          |           |             |  close()   |          |
  |            |          |           |             |----------->| (closed) |
  |            |          |           |             |  raise TransferCancelled
  |            |          |           |   finished(False,"cancelled")       |
  |            |          | "cancelled"<------------|            |          |
  |            |          | scheduleClear(4s)       |            |          |
```

#### 8.5.3 Phone uploads via QR

```
Phone        Browser     Flask (QrWebServer)    MainWindow      Disk
  | scan QR    |                |                    |             |
  |----------->| GET /<tok>/    |                    |             |
  |            |--------------->| render page        |             |
  |            |<---------------| HTML               |             |
  | pick file  |                |                    |             |
  |----------->| POST /upload   |                    |             |
  |            |--------------->| save() ----------->|             |
  |            |                | file_received ---->| history+UI  |
  |            |                |                    |  unique_path|
  |            |                |                    |------------>| .pdf
  |            |<---------------| ok page            |             |
```

#### 8.5.4 Peer discovery on app start (optional, short)

```
MainWindow -> PeerRegistry.start()
PeerRegistry -> Zeroconf: register_service(...)
PeerRegistry -> Zeroconf: ServiceBrowser(handlers=_on_change)
loop every 20s:
   PeerRegistry -> Zeroconf: update_service (heartbeat)
Zeroconf -> PeerRegistry._on_change: peer added/removed
PeerRegistry -> MainWindow: peer_added / peer_removed (signal)
MainWindow -> TransferTab.upsert_peer(peer)
```

### 8.6 Activity Diagram: "Send Files" (UI-driven)

Branches to model: peer-online?, file ≤ 50 GB?, PIN match?, hash valid?,
cancelled?

```
( Start )
   |
   v
[ Add files ]
   |
   v
[ Select peers ]
   |
   v
< Any peer offline? >---yes--> [ Mark "offline" row ] --+
   | no                                                 |
   v                                                    |
[ (optional) Enable PIN ]                               |
   |                                                    |
   v                                                    |
[ Click Send ]                                          |
   |                                                    |
   v                                                    |
[ For each peer: create TransferTask ]                  |
   |                                                    |
   v                                                    |
[ Queue (semaphore=3) ]                                 |
   |                                                    |
   v                                                    |
[ Connect TLS / send offer ]                            |
   |                                                    |
   v                                                    |
< Accepted? >---no--> [ Row: rejected ] ----------------+
   | yes                                                |
   v                                                    |
< PIN ok? >---no----> [ Row: rejected ] ----------------+
   | yes                                                |
   v                                                    |
[ Stream chunks (SHA-256) ]                             |
   |                                                    |
   v                                                    |
< Cancelled? >---yes--> [ Row: cancelled ] -------------+
   | no                                                 |
   v                                                    |
[ Send all_done ]                                       |
   |                                                    |
   v                                                    |
[ Row: done · append history ] <------------------------+
   |
   v
( End )
```

### 8.7 State Machine: TransferTask

States: **Created → Queued → Connecting → AwaitingAccept → Sending → Done**
and side-states **Rejected, Failed, Cancelled** (all terminal).

Transitions:

- `submit()` → Queued
- sema acquired → Connecting
- TLS ok + offer sent → AwaitingAccept
- accept=true (and PIN ok) → Sending
- accept=false / PIN mismatch → Rejected
- connect error / socket error → Failed
- `cancel()` (from any non-terminal) → Cancelled
- last `all_done` sent → Done

### 8.8 State Machine: Receiver per offer

`Listening → Handshaking → AwaitingDecision → Receiving → Verifying →
Completed`, side-states `Rejected, Failed, Cancelled`.

### 8.9 Data Model (entity diagram, very small)

- **Peer** { peer_id (PK, cert fingerprint), name, address, port, status,
  muted }
- **Settings** { device_name, online, download_dir }
- **HistoryEntry** { date, direction, peer, type, count, size }
- **QuickTextMessage** { sender, text, date }

(No relational DB — JSON files; show as «file» stereotypes.)

---

## 9. Wire-protocol cheat sheet (appendix material)

Frame: `1B type | 4B big-endian length | payload (≤ 4 MiB)`.

Types: `J` = JSON-UTF8, `D` = binary data.

JSON envelopes:

| `type`       | Direction         | Fields                                                                                                |
| ------------ | ----------------- | ----------------------------------------------------------------------------------------------------- |
| `offer`      | sender → receiver | `kind` (`files`/`text`/`sync`), `from`, `from_id`, `pin_required`, `files[]`, `total_size`, `folder?` |
| `response`   | receiver → sender | `accept`, `pin?`, `reason?`                                                                           |
| `file_begin` | sender → receiver | `index`, `name`, `size`                                                                               |
| `file_end`   | sender → receiver | `index`, `sha256`                                                                                     |
| `all_done`   | sender → receiver | (none)                                                                                                |
| `text_body`  | sender → receiver | `text`                                                                                                |
| `sync_event` | both              | `op` ∈ {`put`, `put_end`, `delete`, `stop`}, `path?`, `size?`, `sha256?`                              |

Sequence per kind:

- **files**: `offer → response(ok) → (file_begin → N×D → file_end)+ → all_done`
- **text**: `offer → response(ok) → text_body`
- **sync**: `offer → response(ok) → (sync_event …)+`

---

## 10. Testing strategy & coverage (for the testing / validation chapter)

A `pytest` suite under `tests/` mirrors the source modules one-to-one
(`test_<module>.py`). It is engineered to run **without a display, without
a real LAN, and without ever touching the developer's real user data** —
so it is fast, deterministic and CI-friendly.

### 10.1 Test isolation (`conftest.py`)

Before the package is imported, `conftest.py` redirects all persistent
storage to a throw-away temporary directory by overriding the `APPDATA`,
`USERPROFILE` and `HOME` environment variables. Because `config.py`
resolves its data and download folders **at import time**, this guarantees
that no test reads or writes the real `%APPDATA%\p2p_lan_share\`. The same
file makes the package importable from either layout and exposes small
shared fixtures (e.g. `tmp_download_dir`, `free_tcp_port`).

### 10.2 How the networked parts are tested without a network

- **Wire / protocol** — a `socket.socketpair()` provides two connected
  in-process endpoints, so the framing layer is exercised end-to-end with
  no TLS and no ports.
- **Transfers** — real **loopback TLS** on an ephemeral port (patched into
  `config.TCP_PORT`) using the generated self-signed certificate. Qt
  signals are connected with `DirectConnection` because the tests run
  without a Qt event loop.
- **Folder sync** — each side receives one end of a `socketpair` wrapped in
  a `Wire`; the path-traversal guard is called directly.
- **QR web server** — Flask's **test client** drives the HTTP routes, so no
  socket bind or QR image rendering is needed.

### 10.3 What each suite proves

| Test file              | Focus                        | Representative guarantees                                                                                                                                                                           |
| ---------------------- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_native.py`       | **C++ SHA-256 correctness**  | Empty, single-chunk, multi-chunk and 4 MB+ inputs match `hashlib` bit-for-bit; finalising twice raises. Skips cleanly if the library is not built.                                                  |
| `test_protocol.py`     | Wire framing + TLS contexts  | JSON/data round-trips; oversized frame rejected; wrong frame type raises; closed socket → `ConnectionError`; **concurrent writers never interleave** a frame; server/client SSL contexts behave.    |
| `test_network.py`      | Transfer engine (end-to-end) | `FileSpec` / `IncomingOffer` logic; idempotent cancel; **file integrity** (bytes received == bytes sent over TLS); text transfer; offline / muted / rejected offers; queue concurrency cap.         |
| `test_sync.py`         | Folder sync + security       | **Path-traversal** (`../`, absolute, backslash) is neutralised; initial scan mirrors files into the destination; delete events remove files; `_rel` uses forward slashes.                           |
| `test_web_server.py`   | QR phone bridge              | Token gate (404 on a bad token); upload saves the file and emits a signal; "no files" / "too many files" guards; **unique-filename** collision handling; text endpoint truncates to the char limit. |
| `test_crypto_utils.py` | TLS identity                 | Certificate + key are generated once and are **idempotent** on the second call; a valid X.509 with the expected subject; the key is RSA ≥ 2048 bits.                                                |
| `test_discovery.py`    | Peer registry                | Display string (🟢 / 🔴 / 🔇); **fingerprint id is stable** (16 hex chars); mute toggle; device-name fallback when empty; find-by-name.                                                             |
| `test_storage.py`      | Persistence                  | Defaults when the file is missing; partial-file merge; **corrupt JSON falls back** to defaults; thread-safe concurrent append; muted list saved sorted; **atomic write** leaves no `.tmp` behind.   |
| `test_util.py`         | Pure helpers                 | `fmt_size` / `fmt_eta` formatting across unit boundaries; `unique_path` numbering; `local_ip` returns a syntactically valid IPv4.                                                                   |

### 10.4 Markers & execution

`pytest.ini` registers two markers — `integration` (tests that bind real
sockets / use TLS) and `slow` — and points collection at `tests/`. Typical
invocations from the project root:

```
python -m pytest                      # the whole suite
python -m pytest -m integration       # only the end-to-end TLS transfers
python -m pytest tests/test_native.py # a single module
```

### 10.5 Coverage philosophy

Tests target **behaviour and contracts**, not implementation details: the
correctness of the hash, the integrity of transfers, the security guards
(path-traversal, token gate, mute / offline), persistence durability, and
protocol framing under concurrency. GUI widgets are deliberately **not**
unit-tested — they are thin and signal-driven, and the logic they trigger
is already covered on the service side. Two filesystem/timing-sensitive
tests (`test_storage`'s threaded append and one `test_sync` round-trip) can
flake on Windows when an antivirus or the search indexer transiently locks
a file mid-`os.replace`; they pass in isolation and are environmental, not
logic, failures.

---

## 11. Build & deployment (for the implementation chapter)

- Built with **PyInstaller** via `build.py` (Windows & Linux).
- The native library is compiled first by `native/build.py`
  (Windows `p2p_native.dll`, Linux `libp2p_native.so`) and copied into the
  package resource directory so `native.py::_load()` finds it via
  `Path(__file__).parent`.
- `--onefile --windowed`. Output: `dist/P2P LAN Share.exe` on Windows
  (~68 MB) or `dist/P2P LAN Share` on Linux; self-contained, no installer
  required, no admin rights needed.
- Run from source without packaging: `python -m p2p_lan_share.main`
  (after `python native/build.py`).
- Persistent user data lives in `%APPDATA%\p2p_lan_share\` on Windows and
  `~/p2p_lan_share/` on Linux.
- Required firewall rules: TCP 51821 (transfer), TCP 51822 (QR web HTTPS),
  UDP 5353 (mDNS) — Windows prompts on first run.

---

## 12. Suggested thesis outline (so the agent knows where each section lands)

1. **Introduction** — Problem, motivation, contributions, structure.
   _Use §1, §3._
2. **Theoretical background** — P2P, mDNS, TLS, hashing, Qt threading,
   native (C++) integration. _Use §5 (incl. §5.7)._
3. **State of the art** — Comparison table.
   _Use §3._
4. **Requirements analysis** — Functional + non-functional (security,
   performance, UX). _Use §2 + §4._
5. **Architecture & design** — Layers, modules, repository layout,
   patterns, diagrams. _Use §5, §7, §8.1–§8.4._
6. **Implementation** — Wire protocol, transfer FSM, cancellation,
   progress, sync, QR, native SHA-256 module.
   _Use §2, §5.7, §6, §8.5–§8.8, §9._
7. **Robustness & security** — _Use §5.4 + §6._
8. **Testing & validation** — Isolation strategy, networkless testing,
   what each suite proves, markers. _Use §10._
9. **Deployment & user manual** — _Use §11 + screenshots._
10. **Evaluation** — LAN throughput, vs alternatives (qualitative table
    §3), UX heuristics walk-through (§4).
11. **Conclusions & future work** — file resumption, group chat,
    end-to-end pinning, dedicated mobile app.
12. **Appendices** — Protocol cheat sheet (§9), data model (§8.9), build
    instructions (§11).

---

## 13. Glossary (drop-in)

- **mDNS / Zeroconf / Bonjour** — Multicast-DNS service discovery on the
  LAN without a central server.
- **Fingerprint** — SHA-256 of a TLS certificate; uniquely and securely
  identifies a device.
- **Wire** — application-level framed reader/writer over a TLS socket.
- **TLS** — Transport Layer Security; encrypts the transport.
- **PyQt6** — Python bindings for the Qt UI framework.
- **PyInstaller** — packs a Python program + interpreter into a single exe.
- **GIL** — CPython's Global Interpreter Lock; released by `ctypes` calls
  so native code runs truly in parallel with Python.

---

_End of reference document. Everything an author needs to write the
thesis — features, comparisons, UX rationale, theory pegs, diagrams and
appendices — is contained above._
