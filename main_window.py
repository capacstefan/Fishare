import customtkinter as ctk
from tkinter import messagebox, filedialog
from state import AppStatus
from network import TransferService
import threading
import os


# ------ Local UI helpers (merged) ------

class Dialogs:
    @staticmethod
    def pick_files():
        paths = filedialog.askopenfilenames(title="Selecteaza fisierele de trimis")
        return list(paths)

    @staticmethod
    def pick_folder(initial_dir: str):
        return filedialog.askdirectory(title="Alege folderul de descarcare", initialdir=initial_dir)


STATUS_COLORS = {
    "available": "#22c55e",   # verde
    "busy": "#f59e0b",        # galben
    "restricted": "#ef4444",  # rosu
}


class DeviceProgressPanel(ctk.CTkScrollableFrame):
    """Panou scrollabil care afiseaza bare de progres pe device si pe fisier."""

    def __init__(self, master):
        super().__init__(master)
        self._device_rows = {}  # device_id -> DeviceRow

    def update_from_state(self, state):
        # state.progress: dict[device_id][file] = ratio
        current_ids = set(state.progress.keys())

        # remove rows for devices no longer prezente
        for dev_id in list(self._device_rows.keys()):
            if dev_id not in current_ids:
                self._device_rows[dev_id].destroy()
                del self._device_rows[dev_id]

        # update/create rows
        for dev_id, files in state.progress.items():
            row = self._device_rows.get(dev_id)
            if not row:
                name = state.devices.get(dev_id).name if state.devices.get(dev_id) else dev_id
                row = DeviceRow(self, dev_id, name)
                row.pack(fill="x", padx=6, pady=6)
                self._device_rows[dev_id] = row
            row.update_files(files)


class DeviceRow(ctk.CTkFrame):
    """O linie pentru un device, contine titlu si bare per fisier."""

    def __init__(self, master, device_id: str, device_name: str):
        super().__init__(master)
        self.device_id = device_id
        self.device_name = device_name
        self._file_bars = {}  # filename -> (label, bar)

        self.title = ctk.CTkLabel(self, text=f"{device_id}  {device_name}", anchor="w")
        self.title.pack(fill="x", padx=6, pady=(6, 2))

        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="x", padx=6, pady=(0, 6))

    def update_files(self, files_progress: dict):
        # remove bars that no longer exist
        for fname in list(self._file_bars.keys()):
            if fname not in files_progress:
                lbl, bar = self._file_bars.pop(fname)
                lbl.destroy()
                bar.destroy()

        # update/create bars
        for fname, ratio in files_progress.items():
            widgets = self._file_bars.get(fname)
            if not widgets:
                lbl = ctk.CTkLabel(self.container, text=fname, anchor="w")
                lbl.pack(fill="x", padx=4, pady=(2, 0))
                bar = ctk.CTkProgressBar(self.container)
                bar.set(0)
                bar.pack(fill="x", padx=4, pady=(0, 6))
                self._file_bars[fname] = (lbl, bar)
            else:
                lbl, bar = widgets
            # clamp ratio
            r = max(0.0, min(1.0, float(ratio)))
            bar.set(r)


MAX_NAME_LEN = 32


class FIshareApp(ctk.CTk):
    def __init__(self, state, advertiser):
        super().__init__()
        self.title("FIshare")
        self.geometry("1040x680")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.app_state = state
        self.advertiser = advertiser
        # porneste serverul receiver in background
        self.transfer = TransferService(state, ui_root=self)

        # ======= Top bar (nume, status, folder) =======
        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=16, pady=12)

        name_label = ctk.CTkLabel(top, text="Nume dispozitiv")
        name_label.pack(side="left", padx=(8, 6))
        self.name_entry = ctk.CTkEntry(top, width=260)
        self.name_entry.insert(0, self.app_state.cfg.device_name)
        self.name_entry.pack(side="left", padx=(0, 16))
        self.name_entry.bind("<KeyRelease>", self._on_name_change)

        status_label = ctk.CTkLabel(top, text="Stare")
        status_label.pack(side="left", padx=(8, 6))
        self.status_opt = ctk.CTkOptionMenu(
            top,
            values=["available", "busy", "restricted"],
            command=self._on_status,
        )
        self.status_opt.set(self.app_state.status.value)
        self.status_opt.pack(side="left")

        self.dir_btn = ctk.CTkButton(top, text="Folder download", command=self._change_folder)
        self.dir_btn.pack(side="right", padx=8)

        # ======= Body (stanga: dispozitive / dreapta: selectii, progres) =======
        body = ctk.CTkFrame(self)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        # Stanga — dispozitive descoperite (double-click pentru adaugare)
        left = ctk.CTkFrame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)

        ctk.CTkLabel(left, text="Dispozitive in LAN").pack(anchor="w", padx=8, pady=(8, 4))
        self.devices_list = ctk.CTkTextbox(left, height=240)
        self.devices_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.devices_list.bind("<Double-Button-1>", self._add_selected_device)

        # Dreapta — selectii + fisiere + progres detaliat
        right = ctk.CTkFrame(body)
        right.pack(side="right", fill="both", expand=True, padx=(8, 0), pady=8)

        ctk.CTkLabel(right, text="Destinatii selectate (dublu click pentru remove)").pack(anchor="w", padx=8, pady=(8, 4))
        self.selected_list = ctk.CTkTextbox(right, height=120)
        self.selected_list.pack(fill="x", padx=8)
        self.selected_list.bind("<Double-Button-1>", self._remove_selected_device)

        ctk.CTkLabel(right, text="Fisiere selectate").pack(anchor="w", padx=8, pady=(12, 4))
        self.files_list = ctk.CTkTextbox(right, height=140)
        self.files_list.pack(fill="x", padx=8)

        pick = ctk.CTkButton(right, text="Adauga fisiere", command=self._pick_files)
        pick.pack(padx=8, pady=8, anchor="e")

        # Panou progres per device/fisier (bare grafice)
        ctk.CTkLabel(right, text="Progres transfer").pack(anchor="w", padx=8, pady=(8, 4))
        self.progress_panel = DeviceProgressPanel(right)
        self.progress_panel.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # ======= Bottom (trimite) =======
        bottom = ctk.CTkFrame(self)
        bottom.pack(fill="x", padx=16, pady=(0, 16))

        self.send_btn = ctk.CTkButton(bottom, text="Trimite", state="disabled", command=self._send)
        self.send_btn.pack(side="right", padx=8)

        # refresh periodic UI
        self.after(600, self._refresh)

    # --------- Callbacks UI ---------

    def _on_status(self, value):
        self.app_state.set_status(AppStatus(value))

    def _on_name_change(self, _event):
        text = self.name_entry.get()[:MAX_NAME_LEN].strip()
        if not text:
            return
        if text != self.app_state.cfg.device_name:
            self.app_state.cfg.device_name = text
            self.app_state.cfg.save()

    def _change_folder(self):
        newdir = Dialogs.pick_folder(self.app_state.cfg.download_dir)
        if newdir and os.path.isdir(newdir):
            self.app_state.cfg.download_dir = newdir
            self.app_state.cfg.save()
        else:
            messagebox.showerror("Eroare", "Folder invalid sau inaccesibil.")

    def _pick_files(self):
        paths = Dialogs.pick_files()
        # validare existenta fisiere
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            return
        self.app_state.selected_files = list(paths)
        self._refresh_lists()
        self._update_send_btn()

    def _add_selected_device(self, _evt=None):
        # Adauga device-ul aflat pe linia curenta
        idx = self.devices_list.index("insert linestart")
        try:
            line = self.devices_list.get(idx, f"{idx} lineend").strip()
            device_id = line.split(" ")[0]
            dev = self.app_state.devices.get(device_id)
            if not dev:
                return
            if dev.status != AppStatus.AVAILABLE:
                messagebox.showinfo("Indisponibil", "Dispozitivul nu este Available.")
                return
            if device_id not in self.app_state.selected_device_ids:
                self.app_state.selected_device_ids.append(device_id)
                self._refresh_lists()
                self._update_send_btn()
        except Exception:
            pass

    def _remove_selected_device(self, _evt=None):
        idx = self.selected_list.index("insert linestart")
        try:
            line = self.selected_list.get(idx, f"{idx} lineend").strip()
            device_id = line.split(" ")[0]
            if device_id in self.app_state.selected_device_ids:
                self.app_state.selected_device_ids.remove(device_id)
                self._refresh_lists()
                self._update_send_btn()
        except Exception:
            pass

    def _update_send_btn(self):
        if self.app_state.selected_files and self.app_state.selected_device_ids:
            self.send_btn.configure(state="normal")
        else:
            self.send_btn.configure(state="disabled")

    # --------- Loop de refresh ---------

    def _refresh(self):
        self._refresh_lists()
        # actualizeaza progresul din state in panou
        self.progress_panel.update_from_state(self.app_state)
        self.after(600, self._refresh)

    def _refresh_lists(self):
        # devices
        self.devices_list.configure(state="normal")
        self.devices_list.delete("1.0", "end")
        for dev_id, dev in sorted(self.app_state.devices.items()):
            selectable = "" if dev.status == AppStatus.AVAILABLE else " (locked)"
            self.devices_list.insert("end", f"{dev_id}  {dev.name}  [{dev.status.value}]{selectable}\n")
        self.devices_list.configure(state="disabled")

        # selected devices
        self.selected_list.configure(state="normal")
        self.selected_list.delete("1.0", "end")
        for dev_id in self.app_state.selected_device_ids:
            dev = self.app_state.devices.get(dev_id)
            if dev:
                self.selected_list.insert("end", f"{dev_id}  {dev.name}\n")
        self.selected_list.configure(state="disabled")

        # files
        self.files_list.configure(state="normal")
        self.files_list.delete("1.0", "end")
        for p in self.app_state.selected_files:
            self.files_list.insert("end", p + "\n")
        self.files_list.configure(state="disabled")

    # --------- Trimitere ---------

    def _send(self):
        if not (self.app_state.selected_files and self.app_state.selected_device_ids):
            return

        prev_status = self.app_state.status
        self.app_state.set_status(AppStatus.BUSY)
        self._update_send_btn()

        # pregateste lista de device-uri efective
        devices = [self.app_state.devices[d] for d in self.app_state.selected_device_ids if d in self.app_state.devices]

        def worker():
            ok = 0
            for dev in devices:
                if self.transfer.send_to(dev, list(self.app_state.selected_files)):
                    ok += 1
            self.app_state.set_status(prev_status)
            messagebox.showinfo("Rezultat", f"Transfer complet: {ok}/{len(devices)} dispozitive.")

        threading.Thread(target=worker, daemon=True, name="send-ui").start()

