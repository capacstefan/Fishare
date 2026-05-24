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

**P2P LAN Share** is a Windows desktop application for _zero-configuration_
peer-to-peer file, text and folder sharing inside a local network. It
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
- **Phone bridge:** an on-demand QR-code web server lets any phone in the
  same Wi-Fi upload files / send text without installing anything.
- **Folder sync:** one-way watchdog-driven live mirroring.
- **Stack:** Python 3.11, PyQt6, Zeroconf, cryptography, Flask, watchdog,
  qrcode + a C++ native DLL for SHA-256.

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

1. Generates a QR code containing `http://<lan-ip>:51822/<token>/`.
2. The token is a 16-byte `secrets.token_urlsafe` — URLs cannot be guessed.
3. Token check uses `hmac.compare_digest` (timing-safe).
4. Mobile-first responsive HTML (Apple-inspired styling).
5. Upload multiple files at once; up to 500-char quick text.
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
| **Cross-OS / cross-vendor** | Yes.                                    | No.                                          | Yes, with account.                | Windows + any phone with a browser; the protocol is OS-agnostic.       |
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
- **HTTP MAX_CONTENT_LENGTH** to bound phone uploads.
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
| 12  | Phone uploads > limit                              | Flask `MAX_CONTENT_LENGTH` returns HTTP 413.                                                                                                     |
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

| File                    | Responsibility                                                                                  |
| ----------------------- | ----------------------------------------------------------------------------------------------- |
| `main.py`               | Entry point — creates QApplication, applies theme, shows MainWindow.                            |
| `config.py`             | Constants (ports, chunk size, paths, app name).                                                 |
| `util.py`               | Tiny pure helpers (`fmt_size`, `fmt_eta`, `unique_path`, `local_ip`).                           |
| `crypto_utils.py`       | One-time generation of self-signed TLS cert + key.                                              |
| `protocol.py`           | `Wire` framed reader/writer + TLS contexts + `open_offer` handshake helper.                     |
| `discovery.py`          | `PeerRegistry` — mDNS advertise + browse, fingerprint-based identity, mute, heartbeat.          |
| `network.py`            | `TransferServer` (accept loop, receiver), `TransferTask` (sender), `TransferQueue` (semaphore). |
| `sync.py`               | `SyncSender` (initial scan + watchdog), `SyncReceiver` (event consumer, path-traversal guard).  |
| `web_server.py`         | `QrWebServer` — Flask app + QR code generator.                                                  |
| `storage.py`            | Atomic, thread-safe JSON persistence for settings, history, inbox, mute.                        |
| `native.py`             | `ctypes` bridge to `p2p_native.dll` (streaming SHA-256).                                        |
| `native/p2p_native.cpp` | Pure-C++ FIPS-180-4 SHA-256, exposed via C ABI.                                                 |
| `gui/main_window.py`    | Composes services, routes signals, owns life-cycle.                                             |
| `gui/tab_transfer.py`   | File-transfer tab (peer selector, file list, PIN, send, progress rows).                         |
| `gui/tab_quicktext.py`  | Quick-text composer + inbox.                                                                    |
| `gui/tab_tools.py`      | Folder sync + QR web server controls.                                                           |
| `gui/tab_history.py`    | Persistent table view of past transfers.                                                        |
| `gui/dialogs.py`        | Accept-offer, quick-text editor, quick-text reader.                                             |
| `gui/peer_list.py`      | Reusable peer list widget.                                                                      |
| `gui/_widgets.py`       | Generic atoms (`PeerSelector`, headings).                                                       |
| `gui/theme.py`          | Light theme stylesheet + `ToggleSwitch`.                                                        |
| `build_exe.py`          | PyInstaller build script that produces `dist/P2P LAN Share.exe`.                                |

### 7.2 Layers (top-down)

1. **Presentation** — PyQt6 widgets, dialogs, theme.
2. **Application orchestration** — `MainWindow` wires services together.
3. **Services** — `TransferServer/Task/Queue`, `SyncSender/Receiver`,
   `QrWebServer`, `PeerRegistry`.
4. **Protocol** — `Wire`, frame types, JSON envelopes, TLS contexts.
5. **Infrastructure** — `storage`, `crypto_utils`, `native`, OS sockets.

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
                |  HTTP 51822 (token URL, same LAN)
                v
        +---------------+
        |   Phone       |
        |  (any browser)|
        +---------------+
```

Persistent files (per node, under `%APPDATA%\p2p_lan_share\`):
`cert.pem`, `key.pem`, `settings.json`, `history.json`,
`quicktexts.json`, `muted.json`.

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

## 10. Build & deployment (for the implementation chapter)

- Built with **PyInstaller** via `build_exe.py`.
- `--onefile --windowed`, bundles `p2p_native.dll` into the package
  resource directory so `native.py::_load()` finds it via
  `Path(__file__).parent`.
- Output: `dist/P2P LAN Share.exe` (~68 MB), self-contained, no installer
  required, no admin rights needed.
- Persistent user data lives in `%APPDATA%\p2p_lan_share\`.
- Required firewall rules: TCP 51821 (transfer), TCP 51822 (QR web),
  UDP 5353 (mDNS) — Windows prompts on first run.

---

## 11. Suggested thesis outline (so the agent knows where each section lands)

1. **Introduction** — Problem, motivation, contributions, structure.
   _Use §1, §3._
2. **Theoretical background** — P2P, mDNS, TLS, hashing, Qt threading.
   _Use §5._
3. **State of the art** — Comparison table.
   _Use §3._
4. **Requirements analysis** — Functional + non-functional (security,
   performance, UX). _Use §2 + §4._
5. **Architecture & design** — Layers, modules, patterns, diagrams.
   _Use §5, §7, §8.1–§8.4._
6. **Implementation** — Wire protocol, transfer FSM, cancellation,
   progress, sync, QR. _Use §2, §6, §8.5–§8.8, §9._
7. **Robustness & security** — _Use §5.4 + §6._
8. **Deployment & user manual** — _Use §10 + screenshots._
9. **Evaluation** — LAN throughput, vs alternatives (qualitative table
   §3), UX heuristics walk-through (§4).
10. **Conclusions & future work** — multi-platform binary, file
    resumption, group chat, end-to-end pinning, mobile app.
11. **Appendices** — Protocol cheat sheet (§9), data model (§8.9), build
    instructions (§10).

---

## 12. Glossary (drop-in)

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
