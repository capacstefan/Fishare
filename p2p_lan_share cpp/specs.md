Act as an Expert Python Developer.
Your task is to develop a robust, fast, and user-friendly LAN File Transfer application for Windows.

Core Development Principles:

KISS (Keep It Simple, Stupid): Write clean, highly readable code. Do not over-engineer.

Modular but Flat: Split the code into logical modules (e.g., main.py, gui.py, network.py, web_server.py, storage.py and whatever you consider necessary) to avoid a massive single file, but avoid deep nesting or overly complex architectures.

Target OS: Windows only. Optimize file paths and network logic accordingly.

Tech Stack: * GUI: PyQt6 (use its threading models to keep the UI responsive).

Network: standard socket library.

Security: Standard Python ssl module (generate auto-signed certificates on-the-fly for simple, secure End-to-End Encryption).

Web Server (QR feature): A lightweight framework like Flask or FastAPI serving a simple HTML/JS page.

Folder Sync: Choose the simplest, most robust method (e.g., standard watchdog library or a simple periodic directory scan).

Application Features & Requirements:
1. Discovery & State Management:

mDNS Autodiscover: The app broadcasts its device name. The default name is the Windows hostname, but the user can change it.

Online/Offline Toggle: Users can switch states. If Offline: incoming requests are automatically rejected, the user is removed from other peers' "selected" lists, and their mDNS broadcast shows a red status indicator (green when online) to discovered peers.

Mute Peers: Users can right-click/select discovered peers to "Mute" them. Muted peers' requests (files, text, sync) are automatically rejected without showing pop-ups.

2. Networking & Performance:

Queue System: To prevent network bottlenecks, allow a maximum of 4 concurrent TCP connections. Any additional selected peers must wait in a queue.

E2EE & Speed: All transfers must be TLS/SSL encrypted. Implement dynamic chunking/buffering to handle both tiny files (KBs) and large files (<2GB) at maximum LAN speeds.

3. Transfer Mechanics:

PIN Lock (Optional): Before clicking "Send", the sender can check a "PIN Lock" box. This generates a PIN on the sender's screen. The receiver must enter this exact PIN in their accept dialog to start the transfer.

Receiver Pop-ups: When a sender initiates a transfer (text or file), the receiver gets a pop-up: "[Name] wants to send you X files (Total: X MB/GB)". They can Accept or Reject. If offline, it auto-rejects.

4. Sincronization & Tools:

One-Way Folder Sync: A user selects a folder, picks ONE peer, and sends a sync request. The receiver accepts by selecting a local destination folder. Sincronization is unidirectional (Sender -> Receiver). Whatever is added/deleted on the sender's side is mirrored on the receiver's side. Either party can stop the sync (notifying the other).

QR Web Server: A button starts a local web server and generates a QR code on the screen. Scanning it with a phone opens a web page where the phone user can upload files or type text to send directly to the laptop. Pressing "Cancel" stops the server and hides the QR.

5. UI / UX & Layout (PyQt6):

Global Elements: * Bottom of the window: Fade-in/Fade-out notification labels (e.g., "Transfer started", "Folder syncing", "Quick text received").

Data Persistence: Save history, quick texts, muted peers, device name, and last online/offline state to local JSON/SQLite files.

Tab 1: File Transfer

Lists: Discovered Peers, Selected Peers. (Double-click to add/remove from selected, plus a "Clear All" button).

List: Selected Files (Double-click to remove, "Clear All" button).

Buttons: "Add Files", "Choose Default Download Folder", "Send".

Toggles: Online/Offline state, PIN Lock checkbox (and label for generated PIN).

Progress Bars: Displayed per receiver (scrollable if multiple). Must show 0-100%, MB/s speed, and ETA.

Editable label for Device Name.

Tab 2: Quick Text

Lists: Discovered Peers, Selected Peers.

Action: "Write Quick Text" button opens a window (max 500 chars).

Inbox: A list of received texts showing Sender and the first few characters. Clicking an item opens a window to read the full text with a "Copy to Clipboard" button.

Tab 3: Tools

Folder Sync UI: Select one peer from Discovered Peers, "Select Folder" button, "Start Sync" button, "Cancel Sync" button.

QR Server UI: "Host Web Server" button, QR Code display area, "Cancel Server" button.

Tab 4: History

A table/list showing all transfers and texts.

Columns: Date, Size, Number of files, Sent/Received, Peer Name, Type (QuickText/File). For QuickText, leave Size/Number of files as "-".

Instructions for the AI: Begin by setting up the project structure and scaffolding the PyQt6 GUI with the required tabs. Then, implement the core network mDNS logic, followed by the file transfer/queue system. Please provide the code step-by-step or module-by-module so I can easily review and assemble it. Keep the design clean, responsive, and strictly focused on Windows compatibility.