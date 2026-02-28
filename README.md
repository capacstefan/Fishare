# ðŸŸ FIshare

**Fast, secure, P2P file sharing over local networks**

FIshare is a high-performance, encrypted file transfer application for local network use. Share files between computers on the same LAN with automatic peer discovery, end-to-end encryption, and intelligent protocol selection.

---

## âœ¨ Features

### ðŸš€ Performance
- **High-speed transfers:** 50â€“110 MB/s on Gigabit LAN (CPU and disk dependent)
- **Multi-protocol support:** TCP (always available) and QUIC (optional, requires cert files)
- **Automatic protocol selection:** Best common protocol negotiated per peer
- **Binary wire protocol:** Raw encrypted frames â€” no JSON serialisation overhead on data
- **Read-ahead I/O:** Background reader thread overlaps disk reads with network sends for files â‰¥ 2 MB
- **Zero-copy receive:** `recv_into` + `memoryview` â€” no internal buffer copies
- **8 MB socket buffers + TCP_NODELAY:** Eliminates RTT stalls and Nagle delays

### ðŸ”’ Security
- **End-to-end encryption:** ChaCha20-Poly1305 AEAD with incremental nonces
- **Authenticated key exchange:** X25519 ephemeral ECDH + Ed25519 signatures (bounded to exact key sizes)
- **Forward secrecy:** New ephemeral keys per connection
- **Path traversal protection:** Received files always land in the configured download folder
- **Filename deduplication:** Incoming files never silently overwrite existing ones (`file.pdf` â†’ `file(1).pdf`)
- **Partial file cleanup:** Incomplete files deleted automatically on transfer failure
- **Connection limiting:** Semaphore-based DOS protection (max 8 concurrent incoming transfers)
- **Protocol version check:** Mismatched clients receive a clean rejection, not a crash

### ðŸŽ¨ User Experience
- **Zero configuration:** Automatic peer discovery via UDP multicast
- **Parallel transfers:** Each target device gets its own worker thread â€” sending to 3 devices transfers to all 3 simultaneously
- **Per-device send queue:** Pressing Send while a transfer is active queues the new files; they are sent automatically when the current transfer finishes
- **Non-blocking UI:** Send button re-activates immediately after queuing â€” the UI never freezes waiting for a transfer
- **Modern UI:** Clean, Apple-inspired dark theme (PyQt6)
- **Real-time progress:** Per-device progress bars with live MB/s speed display
- **Accurate speed reporting:** Timer starts on first byte sent (after key agreement and Accept dialog), not at button click
- **Transfer history:** Complete record of sent and received transfers
- **Auto-timeout accept dialog:** 30-second timeout â€” never blocks indefinitely
- **Available / Busy toggle:** Instantly stops accepting new incoming transfers

---

## ðŸ“¦ Installation

### Requirements
- **Python:** 3.9 or higher
- **OS:** Windows, macOS, or Linux
- **Network:** Local network with multicast support

### Basic Installation (TCP)
```bash
cd Fishare
pip install -r requirements.txt
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

## ðŸŽ¯ Quick Start

1. **Launch the application:**
   ```bash
   python app.py
   ```

2. **Set your status** to `Available` to accept incoming transfers.

3. **Send files:**
   - Click `ï¼‹ Add Files` to select files (can be clicked multiple times â€” files accumulate)
   - Double-click a file in the list to remove it; click `âœ• Clear All` to reset
   - Double-click a discovered peer to add it to the Send To list
   - Click `Send` â€” transfer starts immediately (or is queued if that device is busy)

4. **Receive files:**
   - Accept or reject the incoming dialog (auto-rejects after 30 s)
   - Files saved to the configured download folder (default: `~/Downloads/FIshare/`)

5. **View history:**
   - Click `ðŸ•˜ History` in the toolbar

---

## ðŸ—ï¸ Architecture

### Component Map

```
app.py              â€” Entry point: wires state, service, GUI, discovery
main_window.py      â€” PyQt6 main window and all UI components
config.py           â€” Config dataclass + JSON persistence
state.py            â€” Thread-safe AppState (_TransferInfo per active transfer)

network.py          â€” Discovery and transfer orchestration
  â”œâ”€ Advertiser     â€” Periodic multicast broadcast (1.5 s interval)
  â”œâ”€ Scanner        â€” Peer discovery, stale device GC
  â””â”€ TransferServiceâ€” Per-device queue workers, protocol selection, retry logic

protocols.py        â€” Protocol abstraction
  â”œâ”€ ProtocolSelector â€” Negotiates best mutual protocol
  â””â”€ TransferProtocol â€” Abstract base (is_available, start_server, send_files)

transfer_tcp.py     â€” TCPProtocol: server loop, _handle_connection, send_files
transfer_quic.py    â€” QUICProtocol: optional, requires cert files + aioquic

security.py         â€” AEADStream (ChaCha20-Poly1305), key_agree() (X25519+Ed25519), Identity (Ed25519)
history.py          â€” TransferRecord dataclass + JSON-backed TransferHistory
history_window.py   â€” History dialog (PyQt6)
```

### Send Flow (Outgoing)

```
User clicks Send
  â””â”€ _do_send() iterates selected devices (instant â€” just enqueues)
       â”œâ”€ Device A: queue empty  â†’ worker thread started â†’ transfer begins now
       â”œâ”€ Device B: transfer active â†’ files appended to queue â†’ sent after current finishes
       â””â”€ Device C: new device  â†’ worker thread started â†’ transfer begins now (parallel with A)

Worker thread (_run_queue_worker):
  while queue not empty:
    pop (device, files)
    _execute_send(device, files)
      â”œâ”€ Re-validate files exist on disk
      â”œâ”€ Re-resolve device from live state (IP may have changed)
      â”œâ”€ Protocol negotiation â†’ select_for_peer()
      â”œâ”€ TCP connect + key exchange (X25519 ECDH)
      â”œâ”€ Send request JSON â†’ wait for Accept
      â”œâ”€ For each file:
      â”‚    send header JSON
      â”‚    if file â‰¥ 2 MB: read-ahead thread + encrypt+send main thread
      â”‚    else: direct read+encrypt+send
      â””â”€ Record to history
```

### Receive Flow (Incoming)

```
TCPProtocol._server_loop() accepts connection
  â””â”€ _handle_connection_wrapper() (semaphore: max 8 concurrent)
       â””â”€ _handle_connection():
            â”œâ”€ key_agree() â€” X25519 + Ed25519 handshake
            â”œâ”€ _handler_callback() â†’ TransferRequestEvent posted to GUI thread
            â”‚    User has 30 s to Accept / Reject (threading.Event.wait â€” no polling)
            â”œâ”€ For each file:
            â”‚    recv header â†’ validate path â†’ open(dest, "wb")
            â”‚    _recv_raw loop â†’ f.write(data) â†’ update_progress()
            â”‚    on error: delete partial file
            â””â”€ Record to history
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
  4. For each file â€” Sender: {file, size} JSON header â†’ raw binary chunks
```

### State Management

`AppState` uses a single `RLock` and one `Dict[str, _TransferInfo]` for all transfer tracking. Presence in the dict means the transfer is active (replaces the previous five parallel dicts + a set). The UI polls a snapshot every 500 ms â€” no direct access to live state from the GUI thread.

---

## ðŸ“Š Performance

| Network | Typical Speed | Notes |
|---------|--------------|-------|
| Gigabit LAN (wired) | 70â€“110 MB/s | SSD-to-SSD |
| WiFi 5 GHz | 40â€“80 MB/s | Distance and interference dependent |
| WiFi 2.4 GHz | 15â€“35 MB/s | QUIC helps with packet loss |

**Key optimisations:**
- 1 MB chunks: fewer syscalls than 64 KB, no memory pressure vs 4 MB
- 8 MB kernel socket buffers: fills Gigabit pipe without stalls
- Single `sendall(header + payload)`: avoids extra TCP segment from two-call approach
- `recv_into + memoryview`: zero-copy receive, no intermediate `bytearray` copies
- Read-ahead thread (files â‰¥ 2 MB): disk I/O and network I/O run in parallel
- Speed timer reset on first byte: displayed MB/s reflects actual network throughput

---

## ðŸ” Security Architecture

| Layer | Algorithm | Purpose |
|-------|-----------|---------|
| Symmetric encryption | ChaCha20-Poly1305 | All data in transit |
| Key exchange | X25519 ephemeral ECDH | Session key derivation |
| Authentication | Ed25519 signatures | Peer public key verification |
| KDF | HKDF-SHA256 | Session key from shared secret |
| Nonce | 12-byte incremental counter | Guaranteed uniqueness |

**Threat model:**

âœ… Protects against: eavesdropping, MITM (with key pinning), path traversal, silent file overwrites, partial file corruption, malformed key exchange, resource exhaustion

âš ï¸ Does not protect against: compromised endpoints, physical storage access, network-layer attacks (router compromise), adversarial peers on the same LAN who have your public key

---

## âš™ï¸ Configuration

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

## ðŸ“ Data Files

| File | Purpose |
|------|---------|
| `Data/config.json` | User settings |
| `Data/id_ed25519.pem` | Persistent Ed25519 identity key (auto-generated) |
| `Data/transfer_history.json` | Transfer log (last 1000 records) |
| `Data/fishare.log` | Rotating application log (5 MB Ã— 5 files) |
| `Data/quic_cert.pem` | TLS certificate for QUIC (user-provided, optional) |
| `Data/quic_key.pem` | TLS private key for QUIC (user-provided, optional) |

---

## ðŸ› Troubleshooting

**No peers discovered**
- Both devices must be on the same subnet
- Allow UDP 49221 through the firewall
- Corporate/guest WiFi often blocks multicast â€” use a personal router

**Transfers fail immediately**
- Allow TCP 49222 (and UDP 49223 for QUIC) through the firewall
- Check `Data/fishare.log` for the specific error

**Transfer rejected / accept dialog not appearing**
- Receiving device may be set to `Busy`
- Accept dialog times out after 30 s â€” check the receiver screen

**Slower than expected**
- Run `iperf3` between devices to verify raw network speed
- Check disk write speed on receiver (`CrystalDiskMark` on Windows)
- SSD â†’ HDD transfers are disk-limited, not network-limited

**QUIC not available**
- Install `aioquic`: `pip install aioquic`
- Generate cert files (see Installation section above)
- Both are required â€” missing either disables QUIC silently

---

## ðŸ“š Dependencies

```
PyQt6 >= 6.6.0          # GUI framework
cryptography >= 43.0.0  # ChaCha20-Poly1305, Ed25519, X25519, HKDF
```

Optional:
```
aioquic >= 0.9.20       # QUIC protocol (also requires TLS cert files)
```

---

## ðŸš€ Planned Enhancements

- **C++ native module (pybind11):** Zero-copy buffer ring, `sendfile(2)` on Linux, scatter-gather I/O â€” targeting 150+ MB/s
- **Transfer resume:** Checkpoint files for interrupted large transfers
- **Bandwidth throttling:** QoS per device or global cap

---

**Happy sharing! ðŸŸ**

