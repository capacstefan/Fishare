import customtkinter as ctk


STATUS_COLORS = {
    "available": "#22c55e",   # verde
    "busy": "#f59e0b",        # galben
    "restricted": "#ef4444",  # roșu
}


class DeviceProgressPanel(ctk.CTkScrollableFrame):
    """Panou scrollabil care afișează bare de progres pe device și pe fișier."""

    def __init__(self, master):
        super().__init__(master)
        self._device_rows = {}  # device_id -> DeviceRow

    def update_from_state(self, state):
        # state.progress: dict[device_id][file] = ratio
        current_ids = set(state.progress.keys())

        # remove rows for devices no longer present
        for dev_id in list(self._device_rows.keys()):
            if dev_id not in current_ids:
                self._device_rows[dev_id].destroy()
                del self._device_rows[dev_id]

        # update/create rows
        for dev_id, files in state.progress.items():
            row = self._device_rows.get(dev_id)
            if not row:
                row = DeviceRow(self, dev_id, state.devices.get(dev_id).name if state.devices.get(dev_id) else dev_id)
                row.pack(fill="x", padx=6, pady=6)
                self._device_rows[dev_id] = row
            row.update_files(files)


class DeviceRow(ctk.CTkFrame):
    """O linie pentru un device, conține titlu și bare per fișier."""

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
