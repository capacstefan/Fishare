import customtkinter as ctk
from tkinter import messagebox
from core.state import AppStatus
from ui.dialogs import Dialogs
from ui.widgets import DeviceProgressPanel
from netcore.transfer import TransferService
import threading
import os


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
        self.transfer = TransferService(state)  # pornește serverul receiver în background

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
            command=self._on_status
        )
        self.status_opt.set(self.app_state.status.value)
        self.status_opt.pack(side="left")

        self.dir_btn = ctk.CTkButton(top, text="Folder download…", command=self._change_folder)
        self.dir_btn.pack(side="right", padx=8)

        # ======= Body (stânga: dispozitive / dreapta: selecții, progres) =======
        body = ctk.CTkFrame(self)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        # Stânga – dispozitive descoperite (double-click pentru adăugare)
        left = ctk.CTkFrame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)

        ctk.CTkLabel(left, text="Dispozitive în LAN").pack(anchor="w", padx=8, pady=(8, 4))
        self.devices_list = ctk.CTkTextbox(left, height=240)
        self.devices_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.devices_list.bind("<Double-Button-1>", self._add_selected_device)

        # Dreapta – selecții + fișiere + progres detaliat
        right = ctk.CTkFrame(body)
        right.pack(side="right", fill="both", expand=True, padx=(8, 0), pady=8)

        ctk.CTkLabel(right, text="Destinații selectate (dublu click pentru remove)").pack(anchor="w", padx=8, pady=(8, 4))
        self.selected_list = ctk.CTkTextbox(right, height=120)
        self.selected_list.pack(fill="x", padx=8)
        self.selected_list.bind("<Double-Button-1>", self._remove_selected_device)

        ctk.CTkLabel(right, text="Fișiere selectate").pack(anchor="w", padx=8, pady=(12, 4))
        self.files_list = ctk.CTkTextbox(right, height=140)
        self.files_list.pack(fill="x", padx=8)

        pick = ctk.CTkButton(right, text="Adaugă fișiere…", command=self._pick_files)
        pick.pack(padx=8, pady=8, anchor="e")

        # Panou progres per device/fișier (bare grafice)
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
        # validare existență fișiere
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            return
        self.app_state.selected_files = list(paths)
        self._refresh_lists()
        self._update_send_btn()

    def _add_selected_device(self, _evt=None):
        # Adaugă device-ul aflat pe linia curentă
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
        # actualizează progresul din state în panou
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

        # pregătește lista de device-uri efective
        devices = [self.app_state.devices[d] for d in self.app_state.selected_device_ids if d in self.app_state.devices]

        def worker():
            ok = 0
            for dev in devices:
                if self.transfer.send_to(dev, list(self.app_state.selected_files)):
                    ok += 1
            self.app_state.set_status(prev_status)
            messagebox.showinfo("Rezultat", f"Transfer complet: {ok}/{len(devices)} dispozitive.")

        threading.Thread(target=worker, daemon=True, name="send-ui").start()
