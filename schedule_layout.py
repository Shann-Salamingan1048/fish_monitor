"""Shared desktop layout for feeding and water-change schedules."""
import tkinter as tk
from tkinter import ttk

import config

FONT_STYLE = "Segoe UI"


def schedule_button(parent, text, command, color, active, foreground="white"):
    return tk.Button(parent, text=text, command=command, font=(FONT_STYLE, 10, "bold"),
                     bg=color, activebackground=active, fg=foreground,
                     activeforeground=foreground, disabledforeground="#94A3B8",
                     relief="flat", padx=14, pady=8, cursor="hand2")


class ScheduleLayout:
    def __init__(self, window, app, title, close):
        self.app = app
        window.title(title)
        window.configure(bg=app.bg_color)
        window.transient(app.root)
        screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
        width = min(screen_w, 900, max(720, int(screen_w * .72)))
        height = min(screen_h, 650, max(500, int(screen_h * .72)))
        window.minsize(min(720, width), min(500, height))
        window.geometry(f"{width}x{height}+{(screen_w-width)//2}+{(screen_h-height)//2}")

        container = tk.Frame(window, bg=app.bg_color)
        container.pack(expand=True, fill="both", padx=22, pady=20)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=1)
        self.header = tk.Frame(container, bg=app.bg_color)
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.header.columnconfigure(0, weight=1)
        tk.Label(self.header, text=title, font=(FONT_STYLE, 20, "bold"),
                 bg=app.bg_color, fg=app.text_main).grid(row=0, column=0, sticky="w")
        tk.Label(self.header, text=f"Recurring schedules • Timezone: {config.FEEDING_TIMEZONE}",
                 font=(FONT_STYLE, 10), bg=app.bg_color, fg=app.text_sub
                 ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        schedule_button(self.header, "Close", close, app.card_soft, app.border_color,
                        app.text_main).grid(row=0, column=1, rowspan=3, sticky="e")
        self.form = tk.Frame(container, bg=app.panel_color, highlightthickness=1,
                             highlightbackground=app.border_color, padx=16, pady=14)
        self.form.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        self.table = tk.Frame(container, bg=app.panel_color, highlightthickness=1,
                              highlightbackground=app.border_color, padx=10, pady=10)
        self.table.grid(row=2, column=0, sticky="nsew")
        self.table.columnconfigure(0, weight=1)
        self.table.rowconfigure(0, weight=1)
        self.actions = tk.Frame(container, bg=app.bg_color)
        self.actions.grid(row=3, column=0, sticky="ew", pady=(12, 0))

        style = ttk.Style(window)
        style.configure("Schedule.Treeview", font=(FONT_STYLE, 10), rowheight=34,
                        background=app.card_color, fieldbackground=app.card_color,
                        foreground=app.text_main)
        style.configure("Schedule.Treeview.Heading", font=(FONT_STYLE, 10, "bold"))
        style.map("Schedule.Treeview", background=[("selected", app.accent_primary)],
                  foreground=[("selected", "white")])

    def field(self, column, label, variable, choices=None, width=8):
        self.form.columnconfigure(column, weight=1)
        padx = (0 if column == 0 else 8, 0)
        tk.Label(self.form, text=label, font=(FONT_STYLE, 10, "bold"),
                 bg=self.app.panel_color, fg=self.app.text_main
                 ).grid(row=0, column=column, sticky="w", padx=padx)
        if choices is None:
            widget = ttk.Entry(self.form, textvariable=variable, width=width, font=(FONT_STYLE, 11))
        else:
            widget = ttk.Combobox(self.form, textvariable=variable, values=choices,
                                  state="readonly", width=width, font=(FONT_STYLE, 11))
        widget.grid(row=1, column=column, sticky="ew", padx=padx, pady=(5, 10))
        return widget

    def day_and_time(self, day, hour, minute):
        return (self.field(0, "Day", day, config.FEEDING_DAY_NAMES, width=20),
                self.field(1, "Hour (24-hour)", hour, tuple(f"{n:02}" for n in range(24))),
                self.field(2, "Minute", minute, tuple(f"{n:02}" for n in range(60))))
