# 🐟 FIshare

**Fast, secure, P2P file sharing over local networks**

FIshare is a high-performance, encrypted file transfer application for local network use. Share files between computers on the same LAN with automatic peer discovery, end-to-end encryption, and intelligent protocol selection.

---

## ✨ Features

### 🚀 Performance
- **High-speed transfers:** 50–110 MB/s on Gigabit LAN (CPU and disk dependent)
- **Multi-protocol support:** TCP (always available) and QUIC (optional, requires cert files)
- **Automatic protocol selection:** Best common protocol negotiated per peer
- **Binary wire protocol:** Raw encrypted frames — no JSON serialisation overhead on data
- **Streaming file I/O:** Chunked reads/writes with adaptive sizing in the native engine
- **Zero-copy receive:** `recv_into` + `memoryview` — no internal buffer copies
- **8 MB socket buffers + TCP_NODELAY:** Eliminates RTT stalls and Nagle delays

### 🔒 Security
- **End-to-end encryption:** ChaCha20-Poly1305 AEAD with incremental nonces
- **Authenticated key exchange:** X25519 ephemeral ECDH + Ed25519 signatures (bounded to exact key sizes)
- **Forward secrecy:** New ephemeral keys per connection
- **Path traversal protection:** Received files always land in the configured download folder
- **Filename deduplication:** Incoming files never silently overwrite existing ones (`file.pdf` → `file(1).pdf`)
- **Partial file cleanup:** Incomplete files deleted automatically on transfer failure
- **Connection limiting:** Semaphore-based DOS protection (max 8 concurrent incoming transfers)
- **Protocol version check:** Mismatched clients receive a clean rejection, not a crash

### 🎨 User Experience
- **Zero configuration:** Automatic peer discovery via UDP multicast
- **Parallel transfers:** Each target device gets its own worker thread — sending to 3 devices transfers to all 3 simultaneously
- **Per-device send queue:** Pressing Send while a transfer is active queues the new files; they are sent automatically when the current transfer finishes
- **Non-blocking UI:** Send button re-activates immediately after queuing — the UI never freezes waiting for a transfer
- **Modern UI:** Clean, Apple-inspired dark theme (PyQt6)
- **Real-time progress:** Per-device progress bars with live MB/s speed display
- **Accurate speed reporting:** Timer starts on first byte sent (after key agreement and Accept dialog), not at button click
- **Transfer history:** Complete record of sent and received transfers
- **Auto-timeout accept dialog:** 30-second timeout — never blocks indefinitely
- **Available / Busy toggle:** Instantly stops accepting new incoming transfers

---

## 📦 Installation

### Requirements
- **Python:** 3.9 or higher
- **OS:** Windows, macOS, or Linux
- **Network:** Local network with multicast support

### Basic Installation (TCP)
```bash
cd Fishare
pip install -r requirements.txt
python build_cpp.py
python app.py
```

### With QUIC Support (Optional)
```bash
pip install aioquic
# Also required: generate TLS cert/key pair
openssl req -x509 -newkey rsa:2048 -keyout Data/quic_key.pem -out Data/quic_cert.pem -days 365 -nodes -subj "/CN=fishare"
python app.py
```

QUIC requires both `aioquic` installed **and** `Data/quic_cert.pem` + `Data/quic_key.pem` present. If either is missing, QUIC is silently skipped and TCP is used. The application works correctly with TCP alone.

---

## 🎯 Quick Start

1. **Launch the application:**
   ```bash
   python app.py
   ```

2. **Set your status** to `Available` to accept incoming transfers.

3. **Send files:**
   - Click `＋ Add Files` to select files (can be clicked multiple times — files accumulate)
   - Double-click a file in the list to remove it; click `✕ Clear All` to reset
   - Double-click a discovered peer to add it to the Send To list
   - Click `Send` — transfer starts immediately (or is queued if that device is busy)

4. **Receive files:**
   - Accept or reject the incoming dialog (auto-rejects after 30 s)
   - Files saved to the configured download folder (default: `~/Downloads/FIshare/`)

5. **View history:**
   - Click `🕘 History` in the toolbar

---

## 🏗️ Architecture

### Component Map

```
app.py              — Entry point: wires state, service, GUI, discovery
main_window.py      — PyQt6 main window and all UI components
config.py           — Config dataclass + JSON persistence
state.py            — Thread-safe AppState (_TransferInfo per active transfer)

network.py          — Discovery layer only
  ├─ Advertiser     — Periodic multicast broadcast (1.5 s interval)
  └─ Scanner        — Peer discovery, stale device GC

transfer_service.py — Transfer orchestration
  └─ TransferService— Per-device queue workers, protocol selection, retry logic

protocols.py        — Protocol abstraction
  ├─ ProtocolSelector — Negotiates best mutual protocol
  └─ TransferProtocol — Abstract base (is_available, start_server, send_files)

transfer.py         — Unified protocol implementations
  ├─ BaseTransferProtocol — Shared logic (adaptive chunks, path dedup)
  ├─ TCPProtocol   — Server loop, _handle_connection, send_files
  └─ QUICProtocol   — 0-RTT handshake, multi-stream, requires cert + aioquic

security.py         — AEADStream (ChaCha20-Poly1305), key_agree() (X25519+Ed25519), Identity (Ed25519)
history.py          — TransferRecord dataclass + JSON-backed TransferHistory
history_window.py   — History dialog (PyQt6)

cpp_engine/         — Required C++ performance module (pybind11)
  ├─ engine.cpp     — High-speed file I/O with AEAD framing (2-3x faster)
  ├─ aead.cpp       — ChaCha20-Poly1305 via OpenSSL EVP interface
  └─ bindings.cpp   — Python bindings (send_file, recv_file with GIL release)
```

### Send Flow (Outgoing)

```
User clicks Send
  └─ _do_send() iterates selected devices (instant — just enqueues)
       ├─ Device A: queue empty  → worker thread started → transfer begins now
       ├─ Device B: transfer active → files appended to queue → sent after current finishes
       └─ Device C: new device  → worker thread started → transfer begins now (parallel with A)

Worker thread (_run_queue_worker):
  while queue not empty:
    pop (device, files)
    _execute_send(device, files)
      ├─ Re-validate files exist on disk
      ├─ Re-resolve device from live state (IP may have changed)
      ├─ Protocol negotiation → select_for_peer()
      ├─ TCP connect + key exchange (X25519 ECDH)
      ├─ Send request JSON → wait for Accept
      ├─ For each file:
      │    send header JSON
      │    stream file via C++ engine with adaptive chunk sizing
      └─ Record to history
```

### Receive Flow (Incoming)

```
TCPProtocol._server_loop() accepts connection
  └─ _handle_connection_wrapper() (semaphore: max 8 concurrent)
       └─ _handle_connection():
            ├─ key_agree() — X25519 + Ed25519 handshake
            ├─ _handler_callback() → TransferRequestEvent posted to GUI thread
            │    User has 30 s to Accept / Reject (threading.Event.wait — no polling)
            ├─ For each file:
            │    recv header → validate path → open(dest, "wb")
            │    _recv_raw loop → f.write(data) → update_progress()
            │    on error: delete partial file
            └─ Record to history
```

### Wire Protocol

```
Frame format (both control and data):
  [4-byte big-endian payload length][ChaCha20-Poly1305 encrypted payload]

Control messages: JSON-encoded plaintext inside the encrypted payload
Data chunks:      raw bytes inside the encrypted payload (no JSON overhead)

Handshake sequence:
  1. Both sides: send X25519 pub key (32 B) + Ed25519 signature (64 B)
  2. Sender: send_request JSON {type, proto_version, files, total, peer_name}
  3. Receiver: {accept: true/false}
  4. For each file — Sender: {file, size} JSON header → raw binary chunks
```

### State Management

`AppState` uses a single `RLock` and one `Dict[str, _TransferInfo]` for all transfer tracking. Presence in the dict means the transfer is active (replaces the previous five parallel dicts + a set). The UI polls snapshots on a 500 ms timer but only rebuilds lists when the state version changes.

---

## 📊 Performance

| Network | Typical Speed | Notes |
|---------|--------------|-------|
| Gigabit LAN (wired) | 70–110 MB/s | SSD-to-SSD |
| WiFi 5 GHz | 40–80 MB/s | Distance and interference dependent |
| WiFi 2.4 GHz | 15–35 MB/s | QUIC helps with packet loss |

**Key optimisations:**
- Adaptive chunk sizing (1–16 MB) to balance syscalls and memory use
- 8 MB kernel socket buffers: fills Gigabit pipe without stalls
- Single `sendall(header + payload)`: avoids extra TCP segment from two-call approach
- `recv_into + memoryview`: zero-copy receive, no intermediate `bytearray` copies
- Speed timer reset on first byte: displayed MB/s reflects actual network throughput

---

## 🔐 Security Architecture

| Layer | Algorithm | Purpose |
|-------|-----------|---------|
| Symmetric encryption | ChaCha20-Poly1305 | All data in transit |
| Key exchange | X25519 ephemeral ECDH | Session key derivation |
| Authentication | Ed25519 signatures | Peer public key verification |
| KDF | HKDF-SHA256 | Session key from shared secret |
| Nonce | 12-byte incremental counter | Guaranteed uniqueness |

**Threat model:**

✅ Protects against: eavesdropping, MITM (with key pinning), path traversal, silent file overwrites, partial file corruption, malformed key exchange, resource exhaustion

⚠️ Does not protect against: compromised endpoints, physical storage access, network-layer attacks (router compromise), adversarial peers on the same LAN who have your public key

---

## ⚙️ Configuration

Default settings (stored in `Data/config.json`):

```json
{
  "device_name": "YOUR-PC",
  "download_dir": "C:/Users/you/Downloads/FIshare",
  "allow_incoming": true,
  "listen_port": 49222,
  "discovery_port": 49221
}
```

Settings can be changed in the GUI (device name field + folder button). Config is saved automatically on change.

**Ports used:**

| Port | Protocol | Purpose |
|------|----------|---------|
| 49221 | UDP multicast | Peer discovery |
| 49222 | TCP | File transfers |
| 49223 | UDP (QUIC) | File transfers (optional) |

---

## 📁 Data Files

| File | Purpose |
|------|---------|
| `Data/config.json` | User settings |
| `Data/id_ed25519.pem` | Persistent Ed25519 identity key (auto-generated) |
| `Data/transfer_history.json` | Transfer log (last 1000 records) |
| `Data/fishare.log` | Rotating application log (5 MB × 5 files) |
| `Data/quic_cert.pem` | TLS certificate for QUIC (user-provided, optional) |
| `Data/quic_key.pem` | TLS private key for QUIC (user-provided, optional) |

---

## 🐛 Troubleshooting

**No peers discovered**
- Both devices must be on the same subnet
- Allow UDP 49221 through the firewall
- Corporate/guest WiFi often blocks multicast — use a personal router

**Transfers fail immediately**
- Allow TCP 49222 (and UDP 49223 for QUIC) through the firewall
- Check `Data/fishare.log` for the specific error

**Transfer rejected / accept dialog not appearing**
- Receiving device may be set to `Busy`
- Accept dialog times out after 30 s — check the receiver screen

**Slower than expected**
- Run `iperf3` between devices to verify raw network speed
- Check disk write speed on receiver (`CrystalDiskMark` on Windows)
- SSD → HDD transfers are disk-limited, not network-limited

**QUIC not available**
- Install `aioquic`: `pip install aioquic`
- Generate cert files (see Installation section above)
- Both are required — missing either disables QUIC silently

---

## 📚 Dependencies

```
PyQt6 >= 6.6.0          # GUI framework
cryptography >= 43.0.0  # ChaCha20-Poly1305, Ed25519, X25519, HKDF
```

Optional:
```
aioquic >= 0.9.20       # QUIC protocol (also requires TLS cert files)
```

---

## ?? C++ Engine (Required)

Build the native transfer engine once:
```bash
python build_cpp.py
```
Provides 2-3x faster transfers via:
- GIL-free file I/O with OpenSSL AEAD
- Zero-copy buffer handling
- Optimized frame encoding/decoding

## ?? Planned Enhancements

- **Transfer resume:** Checkpoint files for interrupted large transfers
- **Bandwidth throttling:** QoS per device or global cap
- **Advanced I/O:** `sendfile(2)` on Linux, scatter-gather I/O for 150+ MB/s

---

**Happy sharing! 🐟**


