"""Desktop editor for scheduled water changes."""
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import config
import water_schedule
from gui_snapshot import SnapshotWorker
from schedule_layout import FONT_STYLE, ScheduleLayout, schedule_button


class WaterScheduleWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.rows = {}
        self.loaded_id = None
        self.busy = False
        self.results = queue.Queue()
        self.after_id = None
        layout = ScheduleLayout(self, app, "Water-change Schedule", self.destroy)
        self.summary = tk.StringVar()
        tk.Label(layout.header, textvariable=self.summary, font=(FONT_STYLE, 10),
                 bg=app.bg_color, fg=app.text_main, justify="left", wraplength=550
                 ).grid(row=2, column=0, sticky="w", pady=(5, 0))

        self.day = tk.StringVar(value=config.FEEDING_DAY_NAMES[0])
        self.hour = tk.StringVar(value="08")
        self.minute = tk.StringVar(value="00")
        self.duration = tk.StringVar()
        self.selectors = layout.day_and_time(self.day, self.hour, self.minute)
        self.entries = (layout.field(3, "Shared duration (seconds)", self.duration),)
        tk.Label(layout.header, text="60 seconds = 1 minute. High water pauses refill; low water pauses draining.\n"
                 "Both resume after 2 seconds of stable Normal. The timer keeps running.",
                 font=(FONT_STYLE, 9), bg=app.bg_color, fg=app.text_sub,
                 justify="left", wraplength=550).grid(row=3, column=0, sticky="w", pady=(5, 0))
        form_actions = tk.Frame(layout.form, bg=app.panel_color)
        form_actions.grid(row=2, column=0, columnspan=5, sticky="ew")
        self.add_button = schedule_button(form_actions, "Add Schedule", self.save,
                                          app.accent_green, "#059669")
        self.save_button = schedule_button(form_actions, "Save Selected Changes",
                                           lambda: self.save(self.selected_id()),
                                           app.accent_primary, "#1D4ED8")
        for button in (self.add_button, self.save_button):
            button.pack(side="left", padx=(0, 8))
        self.message = tk.StringVar(value="Select a schedule below to edit it.")
        tk.Label(form_actions, textvariable=self.message, font=(FONT_STYLE, 9), anchor="w",
                 justify="left", wraplength=265, bg=app.panel_color, fg=app.text_sub
                 ).pack(side="left", fill="x", expand=True)

        columns = ("day", "time", "duration", "state", "last")
        self.tree = ttk.Treeview(layout.table, columns=columns, show="headings",
                                 selectmode="browse", style="Schedule.Treeview", height=5)
        for key, title, width in zip(columns, ("Day", "Time", "Both pumps (s)",
                                              "Status", "Last run"), (170, 75, 110, 80, 120)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=55, anchor="w" if key in ("day", "last") else "center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(layout.table, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.update_selection)
        self.toggle_button = schedule_button(layout.actions, "Pause Selected", self.toggle,
                                             app.accent_warning, "#D97706")
        self.delete_button = schedule_button(layout.actions, "Delete Selected", self.delete,
                                             app.accent_danger, "#DC2626")
        self.stop_button = schedule_button(layout.actions, "Stop water change", self.stop,
                                           app.accent_danger, "#DC2626")
        for button in (self.toggle_button, self.delete_button, self.stop_button):
            button.pack(side="left", padx=(0, 8))
        self.worker = SnapshotWorker(water_schedule.response)
        schedule_button(layout.actions, "Refresh", self.worker.request, app.card_soft,
                        app.border_color, app.text_main).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _: self.destroy())
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.update_selection()
        self.poll()

    def selected_id(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def update_selection(self, _event=None):
        row = self.rows.get(self.selected_id())
        selected_state = "normal" if row and not self.busy else "disabled"
        for button in (self.save_button, self.toggle_button, self.delete_button):
            button.configure(state=selected_state)
        self.toggle_button.configure(text="Enable Selected" if row and not row["enabled"] else "Pause Selected")
        if self.busy:
            return
        if row and self.loaded_id != row["id"]:
            self.day.set(row["dayName"])
            hour, minute = row["time"].split(":")
            self.hour.set(hour)
            self.minute.set(minute)
            self.duration.set(row["durationSeconds"])
        self.loaded_id = row["id"] if row else None

    def set_busy(self, busy):
        self.busy = busy
        for field in self.selectors:
            field.configure(state="disabled" if busy else "readonly")
        for widget in (*self.entries, self.add_button, self.stop_button):
            widget.configure(state="disabled" if busy else "normal")
        self.update_selection()

    def run(self, callback, success_message):
        if self.busy:
            return
        self.set_busy(True)

        def work():
            try:
                callback()
                self.results.put(success_message)
            except Exception as exc:
                self.results.put(str(exc))

        threading.Thread(target=work, daemon=True).start()

    def save(self, schedule_id=None):
        payload = dict(day=config.FEEDING_DAY_NAMES.index(self.day.get()),
                       time=f"{self.hour.get()}:{self.minute.get()}",
                       durationSeconds=self.duration.get())
        self.run(lambda: water_schedule.save(payload, schedule_id), "Water schedule saved.")

    def toggle(self):
        row = self.rows.get(self.selected_id())
        if row:
            self.run(lambda: water_schedule.save({"enabled": not row["enabled"]}, row["id"]),
                     "Schedule updated.")

    def delete(self):
        target = self.selected_id()
        if target is not None and messagebox.askyesno("Delete schedule", "Delete this water-change schedule?", parent=self):
            self.run(lambda: water_schedule.delete(target), "Schedule deleted.")

    def stop(self):
        self.run(water_schedule.stop, "Stop requested.")

    def render(self, data):
        job = data["active"] or data["last"]
        upcoming = data["nextRun"]["dateTime"].replace("T", " ") if data["nextRun"] else "None"
        lines = [f"Next water change: {upcoming}"]
        if not data["ready"]:
            lines.append("Controller disconnected.")
        if job:
            if data['active'] and job['status'] == 'exchanging':
                lines.append(f"{job['controlText']}: about {job['remainingSeconds']}s remaining of {job['durationSeconds']}s")
            else:
                lines.append(f"{'Current' if data['active'] else 'Last'}: {job['status']} {job.get('reason') or ''}".strip())
        self.summary.set("  •  ".join(lines))
        rows = {row["id"]: row for row in data["schedules"]}
        if rows == self.rows and list(rows) == list(self.rows):
            return
        self.rows = rows
        for item in self.tree.get_children():
            if int(item) not in rows:
                self.tree.delete(item)
        for index, (key, row) in enumerate(rows.items()):
            values = (row["dayName"], row["time"], row["durationSeconds"],
                      "Active" if row["enabled"] else "Paused", row["lastStatus"] or "Not run")
            item = str(key)
            if self.tree.exists(item):
                self.tree.item(item, values=values)
                self.tree.move(item, "", index)
            else:
                self.tree.insert("", index, iid=item, values=values)
        self.update_selection()

    def poll(self):
        try:
            message = self.results.get_nowait()
            self.set_busy(False)
            self.message.set(message)
            self.worker.request()
        except queue.Empty:
            pass
        try:
            self.render(self.worker.results.get_nowait())
        except queue.Empty:
            pass
        self.after_id = self.after(100, self.poll)

    def _on_destroy(self, event):
        if event.widget is self:
            self.worker.close()
            if self.after_id:
                self.after_cancel(self.after_id)
