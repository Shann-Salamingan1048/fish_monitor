# tkinter_gui.py
import tkinter as tk
from tkinter import font as tkfont, ttk, messagebox
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import queue
import time
import math
import config
import temperature_units
import sensor_control
import webbrowser
from local_controls import LocalControls
from gui_snapshot import SnapshotWorker
from predictions import format_duration, format_float_status, get_sensor_numeric, build_sensor_alerts

# ============================================================
# THEME COLORS (light matches dashboard mock; dark is the toggled mode)
# ============================================================
ACCENT_PRIMARY = "#2563EB"     # blue (temperature)
ACCENT_GREEN = "#10B981"       # green (water level / safe)
ACCENT_WARNING = "#F59E0B"     # amber (pH)
ACCENT_DANGER = "#EF4444"      # red
ACCENT_PREDICTION = "#8B5CF6"  # purple (turbidity)

FONT_STYLE = "Segoe UI"
GUI_THEME_DB_KEY = "tkinter_gui_theme"

LIGHT_THEME = {
    "bg": "#F4F7FB",
    "panel": "#FFFFFF",
    "card": "#FFFFFF",
    "card_soft": "#F8FAFC",
    "text_main": "#172033",
    "text_sub": "#6B7890",
    "border": "#E5EAF2",
    "mode_icon": "\u2600",
    "mode_text": "Light",
    "info_box_bg": "#EFF6FF",
    "info_box_border": "#DBEAFE",
}

DARK_THEME = {
    "bg": "#0A1020",
    "panel": "#111A2D",
    "card": "#172238",
    "card_soft": "#0D1628",
    "text_main": "#F4F7FC",
    "text_sub": "#A2B0C5",
    "border": "#26354D",
    "mode_icon": "\u263D",
    "mode_text": "Dark",
    "info_box_bg": "#152238",
    "info_box_border": "#1E3A5F",
}

# tint (light background) versions of each accent, used for pills/badges in light mode
LIGHT_TINTS = {
    ACCENT_PRIMARY: "#EFF6FF",
    ACCENT_GREEN: "#ECFDF5",
    ACCENT_WARNING: "#FFFBEB",
    ACCENT_DANGER: "#FEF2F2",
    ACCENT_PREDICTION: "#F5F3FF",
}

# tint (dark background) versions of each accent, used for pills/badges in dark mode
DARK_TINTS = {
    ACCENT_PRIMARY: "#132038",
    ACCENT_GREEN: "#0F2A22",
    ACCENT_WARNING: "#2E2410",
    ACCENT_DANGER: "#2E1518",
    ACCENT_PREDICTION: "#211A38",
}


class RoundedFrame(tk.Canvas):
    """A canvas-backed frame with a rounded rectangle background, optional
    accent left-border, so cards visually match the rounded mock design."""

    def __init__(self, parent, bg, radius=14, border_color=None,
                 left_accent=None, outline_width=1, pad_x=10, pad_y=8, **kwargs):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, **kwargs)
        self._bg = bg
        self._radius = radius
        self._border_color = border_color
        self._left_accent = left_accent
        self._outline_width = outline_width
        self._pad_x = pad_x
        self._pad_y = pad_y
        self._draw_signature = None
        self.inner = tk.Frame(self, bg=bg)
        self.bind("<Configure>", self._redraw)

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _redraw(self, event=None):
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 2 or h < 2:
            return
        signature = (w, h, self._bg, self._border_color, self._left_accent, self._radius)
        if signature == self._draw_signature:
            return
        self._draw_signature = signature
        self.delete("bg")
        outline = self._border_color or self._bg
        self._round_rect(1, 1, w - 2, h - 2, self._radius,
                          fill=self._bg, outline=outline,
                          width=self._outline_width, tags="bg")
        if self._left_accent:
            self.create_rectangle(0, self._radius, 4, h - self._radius,
                                   fill=self._left_accent, outline=self._left_accent,
                                   tags="bg")
        self.tag_lower("bg")
        self.inner.place(x=self._pad_x, y=self._pad_y, relwidth=1, relheight=1,
                          width=-2 * self._pad_x, height=-2 * self._pad_y)


class FocusedMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AquaPi")
        self.root.attributes("-fullscreen", True)

        self.temperature_unit = "F" if config.get_db_value("gui_temperature_unit", "C") == "F" else "C"
        saved_theme = str(config.get_db_value(GUI_THEME_DB_KEY, "light")).strip().lower()
        self.dark_mode = saved_theme == "dark"
        self._apply_theme_vars(DARK_THEME if self.dark_mode else LIGHT_THEME)

        self.accent_primary = ACCENT_PRIMARY
        self.accent_green = ACCENT_GREEN
        self.accent_warning = ACCENT_WARNING
        self.accent_danger = ACCENT_DANGER
        self.accent_prediction = ACCENT_PREDICTION

        self.root.configure(bg=self.bg_color)
        self.root.bind("<Escape>", lambda event: self.root.attributes("-fullscreen", False))

        # remember the most recent sensor update so toggling themes can
        # redraw the UI without losing the current readings
        self._last_update = None

        # Cache rendered states so unchanged sensor cards and notifications
        # are not redrawn for every incoming serial packet.
        self._last_card_state = {}
        self._last_alert_signature = None

        # Serial data arrives on background threads.  Tkinter widgets must
        # only ever be updated by the thread that owns ``mainloop``.
        self._snapshot_worker = SnapshotWorker()
        self._pending_updates = self._snapshot_worker.results
        self._desktop_controls = {"disabled": [], "allDisabled": False, "sensors": {}}
        self.root.bind("<Destroy>", lambda event: self._snapshot_worker.close() if event.widget is self.root else None, add="+")
        self._settings_version_seen = config.settings_version
        self.schedule_ui = None

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.compact_screen = screen_height < 760 or screen_width < 1100
        # Keep wrapping widths smaller than the actual cards on compact Pi displays.
        # The previous 220/420 px minimums could make labels extend past their cards.
        self.value_wrap_length = max(125, int(screen_width * 0.16))
        self.alert_wrap_length = max(260, int(screen_width * 0.38))
        self.value_font_size = 21 if self.compact_screen else 26
        self.value_status_font_size = 10 if self.compact_screen else 11
        self.alert_title_font_size = 13 if self.compact_screen else 14
        self.alert_message_font_size = 11 if self.compact_screen else 12
        self.alert_meta_font_size = 9 if self.compact_screen else 10

        self.value_cards = {}
        self.value_labels = {}
        self.status_pills = {}
        self.status_pill_labels = {}
        self.alert_cards = []

        self._build_ui()
        self._tick_clock()
        self._process_pending_updates()

    # ------------------------------------------------------------------
    # Theme handling
    # ------------------------------------------------------------------
    def _apply_theme_vars(self, theme):
        self.bg_color = theme["bg"]
        self.panel_color = theme["panel"]
        self.card_color = theme["card"]
        self.card_soft = theme["card_soft"]
        self.text_main = theme["text_main"]
        self.text_sub = theme["text_sub"]
        self.border_color = theme["border"]
        self.mode_icon = theme["mode_icon"]
        self.mode_text = theme["mode_text"]
        self.info_box_bg = theme["info_box_bg"]
        self.info_box_border = theme["info_box_border"]

    def _tint(self, color):
        tints = DARK_TINTS if self.dark_mode else LIGHT_TINTS
        fallback = self.card_soft
        return tints.get(color, fallback)


    def _subcomment_color(self):
        """Use white explanatory text in dark mode for maximum readability."""
        return "#FFFFFF" if self.dark_mode else self.text_sub


    def toggle_theme(self, event=None):
        self.dark_mode = not self.dark_mode
        config.set_db_value(GUI_THEME_DB_KEY, "dark" if self.dark_mode else "light")
        self._apply_theme_vars(DARK_THEME if self.dark_mode else LIGHT_THEME)
        self.root.configure(bg=self.bg_color)

        if getattr(self, "controls_window", None) is not None and self.controls_window.winfo_exists():
            self.controls_window.close()
        self._close_schedule_window()
        # rebuild the whole UI with the new theme colors
        for child in self.root.winfo_children():
            child.destroy()
        self.schedule_ui = None
        self._build_ui()

        if self._last_update is not None:
            self._apply_updates(*self._last_update)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        # A theme change destroys every Tk widget. Clear all widget registries
        # before rebuilding so predictions/alerts never point to destroyed widgets.
        self.value_cards = {}
        self.value_labels = {}
        self.status_pills = {}
        self.status_pill_labels = {}
        self.alert_cards = []

        # New widgets must receive one complete render even if their values are
        # identical to the previous theme/layout.
        self._last_card_state = {}
        self._last_alert_signature = None

        main_frame = tk.Frame(self.root, bg=self.bg_color)
        main_frame.pack(expand=True, fill="both", padx=28, pady=(22, 14))
        main_frame.grid_rowconfigure(2, weight=1)
        # Give sensor cards enough horizontal room for full values and comments.
        main_frame.grid_columnconfigure(0, weight=1, uniform="main")
        main_frame.grid_columnconfigure(1, weight=1, uniform="main")

        # Header
        header = RoundedFrame(main_frame, bg=self.panel_color, radius=20,
                              border_color=self.border_color,
                              height=86 if self.compact_screen else 96,
                              pad_x=20, pad_y=12)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        header = header.inner
        header.grid_columnconfigure(0, weight=1)

        tk.Label(
            header, text="FISH MONITOR • LOCAL DASHBOARD",
            font=(FONT_STYLE, 9, "bold"), bg=self.panel_color,
            fg=self.accent_green, anchor="w"
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))
        title_label = tk.Label(
            header,
            text="AquaPi",
            font=(FONT_STYLE, 24, "bold"),
            bg=self.panel_color,
            fg=self.text_main,
            anchor="w"
        )
        title_label.grid(row=1, column=0, sticky="w")
        tk.Label(
            header, text="Real-time water quality, level, and early-warning insights",
            font=(FONT_STYLE, 10), bg=self.panel_color, fg=self.text_sub, anchor="w"
        ).grid(row=2, column=0, sticky="w", pady=(2, 0))

        toolbar = tk.Frame(main_frame, bg=self.bg_color)
        toolbar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        # Light/Dark mode toggle pill (click to switch theme)
        mode_pill = RoundedFrame(toolbar, bg=self.panel_color, radius=16,
                                  border_color=self.border_color, height=32, width=104,
                                  pad_x=8, pad_y=4)
        schedule_button = tk.Button(
            toolbar,
            text="Schedule" if self.compact_screen else "Feeding Schedule",
            command=self.open_feeding_schedule_window,
            font=(FONT_STYLE, 10, "bold"),
            bg=self.accent_green,
            fg="#FFFFFF",
            activebackground="#059669",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
        )
        schedule_button.grid(row=0, column=0, sticky="e", padx=(10, 8))

        tk.Button(toolbar, text="Controls & settings", command=self.open_controls_window,
                  font=(FONT_STYLE, 10, "bold"), bg=self.accent_primary, fg="white",
                  relief="flat", padx=10, pady=7).grid(row=0, column=1, padx=6)
        tk.Button(toolbar, text="Open web", command=lambda: webbrowser.open(f"http://localhost:{config.WEB_PORT}"),
                  relief="flat", padx=8, pady=7).grid(row=0, column=2, sticky="e", padx=6)
        mode_pill.grid(row=0, column=3, sticky="e", padx=(0, 8))
        mode_pill.grid_propagate(False)
        mode_label = tk.Label(mode_pill.inner, text=f"{self.mode_icon}  {self.mode_text}",
                               font=(FONT_STYLE, 11, "bold"),
                               bg=self.panel_color, fg=self.accent_primary, cursor="hand2")
        mode_label.pack(expand=True)
        mode_pill.config(cursor="hand2")
        mode_pill.bind("<Button-1>", self.toggle_theme)
        mode_label.bind("<Button-1>", self.toggle_theme)

        self.clock_label = tk.Label(
            header,
            text="--:--:--",
            font=(FONT_STYLE, 15, "bold"),
            bg=self.panel_color,
            fg=self.accent_primary,
            anchor="e"
        )
        self.clock_label.grid(row=1, column=3, sticky="e", padx=(10, 0))
        header.grid_columnconfigure(3, weight=0)
        unit_frame = tk.Frame(header, bg=self.panel_color)
        unit_frame.grid(row=2, column=3, sticky='e')
        tk.Label(unit_frame, text='Temperature ', bg=self.panel_color, fg=self.text_sub).pack(side='left')
        self.temperature_unit_var = tk.StringVar(value='Fahrenheit (°F)' if self.temperature_unit == 'F' else 'Celsius (°C)')
        unit_select = ttk.Combobox(unit_frame, textvariable=self.temperature_unit_var,
                                  values=('Celsius (°C)', 'Fahrenheit (°F)'), state='readonly', width=15)
        unit_select.pack(side='left')
        unit_select.bind('<<ComboboxSelected>>', self.change_temperature_unit)

        # Values panel
        self.values_panel = self._create_panel(main_frame, "Sensor readings", "Live conditions across your tank")
        self.values_panel.grid(row=2, column=0, sticky="nsew", padx=(0, 8))
        self.values_panel.grid_rowconfigure(1, weight=1)
        self.values_panel.grid_columnconfigure(0, weight=1)

        values_grid = tk.Frame(self.values_panel, bg=self.panel_color)
        values_grid.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        values_grid.grid_columnconfigure(0, weight=1, uniform="values")
        values_grid.grid_columnconfigure(1, weight=1, uniform="values")
        values_grid.grid_rowconfigure(0, weight=1, uniform="values")
        values_grid.grid_rowconfigure(1, weight=1, uniform="values")

        self._create_value_card(
            values_grid, "temp", 0, 0,
            "Water Temperature", "T",
            self._format_temp_display(None), "Waiting for temperature reading",
            self.accent_primary
        )
        self._create_value_card(
            values_grid, "water", 0, 1,
            "Water Level", "W",
            "--", "Float sensor status",
            self.accent_green
        )
        self._create_value_card(
            values_grid, "turbidity", 1, 0,
            "Turbidity", "TB",
            "--", "Water clarity reading",
            self.accent_prediction
        )
        self._create_value_card(
            values_grid, "ph", 1, 1,
            "pH Level", "pH",
            "--", "Acidity / alkalinity reading",
            self.accent_warning
        )

        # Notifications panel
        self.notifications_panel = self._create_panel(
            main_frame, "Smart notifications", "Alerts and trends", show_badge=True
        )
        self.notifications_panel.grid(row=2, column=1, sticky="nsew", padx=(8, 0))
        self.notifications_panel.grid_rowconfigure(3, weight=1)
        self.notifications_panel.grid_columnconfigure(0, weight=1)

        self._create_status_legend(self.notifications_panel)

        self.alert_list = tk.Frame(self.notifications_panel, bg=self.panel_color)
        self.alert_list.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.alert_list.grid_columnconfigure(0, weight=1)
        for index in range(config.MAX_TK_ALERTS):
            self.alert_list.grid_rowconfigure(index, weight=1)

        self._create_empty_alert()

        for index in range(config.MAX_TK_ALERTS):
            card = self._create_alert_card(self.alert_list, index)
            self.alert_cards.append(card)

        self.footer_label = tk.Label(
            main_frame,
            text="Press Esc to exit fullscreen",
            font=(FONT_STYLE, 10),
            bg=self.bg_color,
            fg=self.text_sub,
        )
        self.footer_label.grid(row=3, column=0, columnspan=2, pady=(6, 0), sticky="ew")

    # ------------------------------------------------------------------
    # Daily/weekly feeding schedule window
    # ------------------------------------------------------------------
    def open_controls_window(self):
        window = getattr(self, 'controls_window', None)
        if window is not None and window.winfo_exists():
            window.lift()
        else:
            self.controls_window = LocalControls(self.root, self.open_feeding_schedule_window, self.set_temperature_unit)

    def open_feeding_schedule_window(self):
        if self.schedule_ui:
            window = self.schedule_ui.get("window")
            try:
                if window and window.winfo_exists():
                    window.deiconify()
                    window.lift()
                    window.focus_force()
                    self._refresh_schedule_tree()
                    return
            except tk.TclError:
                pass

        window = tk.Toplevel(self.root)
        window.title("Feeding Schedule")
        window.configure(bg=self.bg_color)
        window.minsize(720, 500)
        window.resizable(True, True)
        window.transient(self.root)

        width = min(900, max(720, int(self.root.winfo_screenwidth() * 0.72)))
        height = min(650, max(500, int(self.root.winfo_screenheight() * 0.72)))
        x_pos = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y_pos = max(0, (self.root.winfo_screenheight() - height) // 2)
        window.geometry(f"{width}x{height}+{x_pos}+{y_pos}")

        container = tk.Frame(window, bg=self.bg_color)
        container.pack(expand=True, fill="both", padx=22, pady=20)
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(2, weight=1)

        header = tk.Frame(container, bg=self.bg_color)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.grid_columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="Feeding Schedule",
            font=(FONT_STYLE, 20, "bold"),
            bg=self.bg_color,
            fg=self.text_main,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text=f"Recurring schedules • Timezone: {config.FEEDING_TIMEZONE}",
            font=(FONT_STYLE, 10),
            bg=self.bg_color,
            fg=self.text_sub,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        tk.Button(
            header,
            text="Close",
            command=self._close_schedule_window,
            font=(FONT_STYLE, 10, "bold"),
            bg=self.card_soft,
            fg=self.text_main,
            activebackground=self.border_color,
            relief="flat",
            padx=14,
            pady=7,
            cursor="hand2",
        ).grid(row=0, column=1, rowspan=4, sticky="e")

        form = tk.Frame(
            container,
            bg=self.panel_color,
            highlightthickness=1,
            highlightbackground=self.border_color,
            padx=16,
            pady=14,
        )
        form.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=1)
        form.grid_columnconfigure(2, weight=1)

        cycles_var = tk.StringVar(value="3")
        day_var = tk.StringVar(value=config.FEEDING_DAY_NAMES[0])
        hour_var = tk.StringVar(value="08")
        minute_var = tk.StringVar(value="00")
        status_var = tk.StringVar(
            value="Add a new schedule, or select one below to edit its day and time."
        )
        current_date_var = tk.StringVar()

        tk.Label(
            header,
            textvariable=current_date_var,
            font=(FONT_STYLE, 10),
            bg=self.bg_color,
            fg=self.text_main,
            anchor="w",
        ).grid(row=2, column=0, sticky="w", pady=(5, 0))

        self._create_schedule_field_label(form, "Day", 0)
        day_input = ttk.Combobox(
            form,
            textvariable=day_var,
            values=config.FEEDING_DAY_NAMES,
            state="readonly",
            font=(FONT_STYLE, 11),
        )
        day_input.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(5, 10))

        self._create_schedule_field_label(form, "Hour (24-hour)", 1)
        hour_input = ttk.Combobox(
            form,
            textvariable=hour_var,
            values=tuple(f"{value:02d}" for value in range(24)),
            state="readonly",
            font=(FONT_STYLE, 11),
        )
        hour_input.grid(row=1, column=1, sticky="ew", padx=8, pady=(5, 10))

        self._create_schedule_field_label(form, "Minute", 2)
        minute_input = ttk.Combobox(
            form,
            textvariable=minute_var,
            values=tuple(f"{value:02d}" for value in range(60)),
            state="readonly",
            font=(FONT_STYLE, 11),
        )
        minute_input.grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=(5, 10))

        form.grid_columnconfigure(3, weight=1)
        self._create_schedule_field_label(form, "Cycles (whole number)", 3)
        ttk.Entry(form, textvariable=cycles_var, width=12).grid(row=1, column=3, padx=8, sticky="ew")
        form_buttons = tk.Frame(form, bg=self.panel_color)
        form_buttons.grid(row=2, column=0, columnspan=4, sticky="ew")
        tk.Button(
            form_buttons,
            text="Add Schedule",
            command=self._add_gui_schedule,
            font=(FONT_STYLE, 10, "bold"),
            bg=self.accent_green,
            fg="#FFFFFF",
            activebackground="#059669",
            activeforeground="#FFFFFF",
            relief="flat",
            padx=15,
            pady=8,
            cursor="hand2",
        ).pack(side="left", padx=(0, 8))
        save_button = tk.Button(
            form_buttons,
            text="Save Selected Changes",
            command=self._save_gui_schedule_changes,
            font=(FONT_STYLE, 10, "bold"),
            bg=self.accent_primary,
            fg="#FFFFFF",
            activebackground="#1D4ED8",
            activeforeground="#FFFFFF",
            disabledforeground="#94A3B8",
            relief="flat",
            padx=15,
            pady=8,
            state="disabled",
        )
        save_button.pack(side="left", padx=(0, 8))
        tk.Label(
            form_buttons,
            textvariable=status_var,
            font=(FONT_STYLE, 9),
            bg=self.panel_color,
            fg=self.text_sub,
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(8, 0))

        table_frame = tk.Frame(
            container,
            bg=self.panel_color,
            highlightthickness=1,
            highlightbackground=self.border_color,
            padx=10,
            pady=10,
        )
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        style = ttk.Style(window)
        style.configure(
            "FeedingSchedule.Treeview",
            font=(FONT_STYLE, 10),
            rowheight=34,
            background=self.card_color,
            fieldbackground=self.card_color,
            foreground=self.text_main,
        )
        style.configure(
            "FeedingSchedule.Treeview.Heading",
            font=(FONT_STYLE, 10, "bold"),
        )
        style.map(
            "FeedingSchedule.Treeview",
            background=[("selected", self.accent_primary)],
            foreground=[("selected", "#FFFFFF")],
        )

        columns = ("day", "time", "cycles", "next_feed", "status", "last_feed")
        tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="FeedingSchedule.Treeview",
        )
        tree.heading("day", text="Day")
        tree.heading("time", text="Time")
        tree.heading("cycles", text="Cycles")
        tree.column("cycles", width=60, minwidth=50, anchor="center")
        tree.heading("next_feed", text="Next Feed Date")
        tree.heading("status", text="Status")
        tree.heading("last_feed", text="Last Feed Sent")
        tree.column("day", width=105, minwidth=90, anchor="w")
        tree.column("time", width=95, minwidth=85, anchor="center")
        tree.column("next_feed", width=195, minwidth=170, anchor="w")
        tree.column("status", width=80, minwidth=70, anchor="center")
        tree.column("last_feed", width=190, minwidth=160, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        tree.bind("<<TreeviewSelect>>", self._update_schedule_selection_ui)

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)

        actions = tk.Frame(container, bg=self.bg_color)
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        toggle_button = tk.Button(
            actions,
            text="Select a Schedule",
            command=self._toggle_gui_schedule,
            font=(FONT_STYLE, 10, "bold"),
            bg=self.accent_warning,
            fg="#FFFFFF",
            activebackground="#D97706",
            activeforeground="#FFFFFF",
            relief="flat",
            padx=14,
            pady=8,
            state="disabled",
        )
        toggle_button.pack(side="left", padx=(0, 8))
        tk.Button(
            actions,
            text="Delete Selected",
            command=self._delete_gui_schedule,
            font=(FONT_STYLE, 10, "bold"),
            bg=self.accent_danger,
            fg="#FFFFFF",
            activebackground="#DC2626",
            activeforeground="#FFFFFF",
            relief="flat",
            padx=14,
            pady=8,
            cursor="hand2",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            actions,
            text="Refresh",
            command=self._refresh_schedule_tree,
            font=(FONT_STYLE, 10, "bold"),
            bg=self.card_soft,
            fg=self.text_main,
            activebackground=self.border_color,
            relief="flat",
            padx=14,
            pady=8,
            cursor="hand2",
        ).pack(side="right")

        self.schedule_ui = {
            "window": window,
            "tree": tree,
            "day_var": day_var,
            "cycles_var": cycles_var,
            "hour_var": hour_var,
            "minute_var": minute_var,
            "status_var": status_var,
            "current_date_var": current_date_var,
            "save_button": save_button,
            "toggle_button": toggle_button,
            "loaded_schedule_id": None,
        }
        window.protocol("WM_DELETE_WINDOW", self._close_schedule_window)
        window.bind("<Escape>", lambda _event: self._close_schedule_window())
        self._refresh_schedule_tree()
        self._schedule_window_auto_refresh()

    def _create_schedule_field_label(self, parent, text, column):
        tk.Label(
            parent,
            text=text,
            font=(FONT_STYLE, 10, "bold"),
            bg=self.panel_color,
            fg=self.text_main,
            anchor="w",
        ).grid(row=0, column=column, sticky="w", padx=(0 if column == 0 else 8, 0))

    def _close_schedule_window(self):
        if not self.schedule_ui:
            return
        window = self.schedule_ui.get("window")
        self.schedule_ui = None
        try:
            if window and window.winfo_exists():
                window.destroy()
        except tk.TclError:
            pass

    def _schedule_window_auto_refresh(self):
        if not self.schedule_ui:
            return
        window = self.schedule_ui.get("window")
        try:
            if not window or not window.winfo_exists():
                self.schedule_ui = None
                return
            self._refresh_schedule_tree(preserve_message=True)
            window.after(5000, self._schedule_window_auto_refresh)
        except tk.TclError:
            self.schedule_ui = None

    def _selected_gui_schedule(self):
        if not self.schedule_ui:
            return None
        tree = self.schedule_ui["tree"]
        selection = tree.selection()
        if not selection:
            self.schedule_ui["status_var"].set("Select a schedule first.")
            return None
        return config.get_feeding_schedule(int(selection[0]))

    def _update_schedule_selection_ui(self, _event=None, update_message=True, load_form=True):
        if not self.schedule_ui:
            return
        tree = self.schedule_ui["tree"]
        selection = tree.selection()
        button = self.schedule_ui["toggle_button"]
        save_button = self.schedule_ui["save_button"]
        if not selection:
            button.configure(text="Select a Schedule", state="disabled", cursor="")
            save_button.configure(state="disabled", cursor="")
            self.schedule_ui["loaded_schedule_id"] = None
            return

        schedule = config.get_feeding_schedule(int(selection[0]))
        if not schedule:
            button.configure(text="Select a Schedule", state="disabled", cursor="")
            save_button.configure(state="disabled", cursor="")
            self.schedule_ui["loaded_schedule_id"] = None
            return

        save_button.configure(state="normal", cursor="hand2")
        if load_form and self.schedule_ui["loaded_schedule_id"] != schedule["id"]:
            hour, minute = schedule["time"].split(":")
            self.schedule_ui["day_var"].set(schedule["dayName"])
            self.schedule_ui["hour_var"].set(hour)
            self.schedule_ui["minute_var"].set(minute)
            self.schedule_ui["cycles_var"].set(str(schedule["cycles"]))
            self.schedule_ui["loaded_schedule_id"] = schedule["id"]

        if schedule["enabled"]:
            button.configure(
                text="Pause Selected",
                state="normal",
                bg=self.accent_warning,
                activebackground="#D97706",
                cursor="hand2",
            )
            action_text = "Pause Selected to stop it."
        else:
            button.configure(
                text="Enable Selected",
                state="normal",
                bg=self.accent_green,
                activebackground="#059669",
                cursor="hand2",
            )
            action_text = "Enable Selected before it can feed."

        if update_message:
            self.schedule_ui["status_var"].set(
                f"Selected {schedule['dayName']} at "
                f"{self._format_schedule_time(schedule['time'])}. "
                f"Change the fields above and choose Save Selected Changes, or {action_text}"
            )

    def _schedule_form_values(self):
        day_name = self.schedule_ui["day_var"].get()
        if day_name not in config.FEEDING_DAY_NAMES:
            raise ValueError("Select a valid day")
        day = config.FEEDING_DAY_NAMES.index(day_name)
        feed_time = (
            f"{self.schedule_ui['hour_var'].get()}:"
            f"{self.schedule_ui['minute_var'].get()}"
        )
        return day, config.validate_feed_time(feed_time)

    def _add_gui_schedule(self):
        if not self.schedule_ui:
            return
        try:
            day, feed_time = self._schedule_form_values()
            schedule = config.create_feeding_schedule(day, feed_time, True, self.schedule_ui["cycles_var"].get())
            self.schedule_ui["status_var"].set(
                f"Added {schedule['dayName']} at {self._format_schedule_time(feed_time)}."
            )
            self._refresh_schedule_tree(preserve_message=True, select_id=schedule["id"])
        except ValueError as exc:
            messagebox.showerror("Schedule Error", str(exc), parent=self.schedule_ui["window"])

    def _save_gui_schedule_changes(self):
        if not self.schedule_ui:
            return
        selected = self._selected_gui_schedule()
        if not selected:
            return
        try:
            day, feed_time = self._schedule_form_values()
            schedule = config.update_feeding_schedule(
                selected["id"], day, feed_time, selected["enabled"], self.schedule_ui["cycles_var"].get()
            )
            self.schedule_ui["loaded_schedule_id"] = schedule["id"]
            self.schedule_ui["status_var"].set(
                f"Saved changes: {schedule['dayName']} at "
                f"{self._format_schedule_time(schedule['time'])}."
            )
            self._refresh_schedule_tree(preserve_message=True, select_id=schedule["id"])
        except ValueError as exc:
            messagebox.showerror("Schedule Error", str(exc), parent=self.schedule_ui["window"])

    def _toggle_gui_schedule(self):
        if not self.schedule_ui:
            return
        selected = self._selected_gui_schedule()
        if not selected:
            return
        try:
            schedule = config.update_feeding_schedule(
                selected["id"], selected["day"], selected["time"], not selected["enabled"]
            )
            action = "Enabled" if schedule["enabled"] else "Paused"
            self.schedule_ui["status_var"].set(f"{action} the selected schedule.")
            self._refresh_schedule_tree(preserve_message=True, select_id=schedule["id"])
        except ValueError as exc:
            messagebox.showerror("Schedule Error", str(exc), parent=self.schedule_ui["window"])

    def _delete_gui_schedule(self):
        if not self.schedule_ui:
            return
        selected = self._selected_gui_schedule()
        if not selected:
            return
        confirmed = messagebox.askyesno(
            "Delete Feeding Schedule",
            f"Delete {selected['dayName']} at {self._format_schedule_time(selected['time'])}?",
            parent=self.schedule_ui["window"],
        )
        if not confirmed:
            return
        config.delete_feeding_schedule(selected["id"])
        self.schedule_ui["status_var"].set("Feeding schedule deleted.")
        self._refresh_schedule_tree(preserve_message=True)

    def _refresh_schedule_tree(self, preserve_message=False, select_id=None):
        if not self.schedule_ui:
            return
        tree = self.schedule_ui["tree"]
        existing_selection = tree.selection()
        if select_id is None and existing_selection:
            select_id = int(existing_selection[0])

        for item_id in tree.get_children():
            tree.delete(item_id)

        schedules = config.get_feeding_schedules()
        zone = self._get_feeding_zone()
        now = datetime.now(zone)
        self.schedule_ui["current_date_var"].set(
            "Date: " + now.strftime("%A, %B %d, %Y").replace(" 0", " ")
        )

        for schedule in schedules:
            status = "Active" if schedule["enabled"] else "Paused"
            last_feed = self._format_schedule_last_feed(schedule)
            tree.insert(
                "",
                "end",
                iid=str(schedule["id"]),
                values=(
                    schedule["dayName"],
                    self._format_schedule_time(schedule["time"]),
                    schedule["cycles"],
                    self._format_schedule_next_feed(schedule, now),
                    status,
                    last_feed,
                ),
            )

        if select_id is not None and tree.exists(str(select_id)):
            tree.selection_set(str(select_id))
            tree.focus(str(select_id))
            tree.see(str(select_id))

        self._update_schedule_selection_ui(update_message=False, load_form=False)

        if not preserve_message:
            active_count = sum(1 for schedule in schedules if schedule["enabled"])
            self.schedule_ui["status_var"].set(
                f"{active_count} active schedule(s) • {len(schedules)} total."
            )

    def _format_schedule_time(self, feed_time):
        return datetime.strptime(feed_time, "%H:%M").strftime("%I:%M %p").lstrip("0")

    def _get_feeding_zone(self):
        try:
            return ZoneInfo(config.FEEDING_TIMEZONE)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def _format_schedule_next_feed(self, schedule, now=None):
        if not schedule.get("enabled"):
            return "Paused"

        zone = self._get_feeding_zone()
        local_now = now or datetime.now(zone)
        if local_now.tzinfo is None:
            local_now = local_now.replace(tzinfo=zone)
        else:
            local_now = local_now.astimezone(zone)

        hour, minute = (int(part) for part in schedule["time"].split(":"))
        daily = int(schedule["day"]) == config.EVERY_DAY
        days_ahead = 0 if daily else (int(schedule["day"]) - local_now.weekday()) % 7
        candidate = (local_now + timedelta(days=days_ahead)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        due_now = False
        if candidate <= local_now:
            seconds_late = (local_now - candidate).total_seconds()
            already_triggered = schedule.get("lastTriggeredDate") == candidate.date().isoformat()
            if seconds_late <= config.FEEDING_GRACE_SECONDS and not already_triggered:
                due_now = True
            else:
                candidate += timedelta(days=1 if daily else 7)

        date_text = candidate.strftime("%A, %b %d, %Y").replace(" 0", " ")
        return f"Due now • {date_text}" if due_now else date_text

    def _format_schedule_last_feed(self, schedule):
        if schedule.get("lastStatus") not in {"success", "complete"} or not schedule.get("lastTriggeredAt"):
            return "Last: " + str(schedule.get("lastStatus") or "No serving yet")
        zone = self._get_feeding_zone()
        triggered = datetime.fromtimestamp(float(schedule["lastTriggeredAt"]), tz=zone)
        return triggered.strftime("%b %d, %Y • %I:%M %p").replace(" 0", " ")

    # ------------------------------------------------------------------
    # Panels / cards / pills
    # ------------------------------------------------------------------
    def _create_panel(self, parent, title, subtitle="", show_badge=False):
        panel = RoundedFrame(parent, bg=self.panel_color, radius=18,
                              border_color=self.border_color)
        inner = panel.inner
        inner.grid_columnconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(0, weight=0)

        title_frame = tk.Frame(inner, bg=self.panel_color)
        title_frame.grid(row=0, column=0, sticky="ew", padx=2, pady=(6, 10))
        title_frame.grid_columnconfigure(0, weight=1)

        label = tk.Label(
            title_frame,
            text=title,
            font=(FONT_STYLE, 16, "bold"),
            bg=self.panel_color,
            fg=self.text_main,
            anchor="w"
        )
        label.grid(row=0, column=0, sticky="w")

        if show_badge:
            sensor_status = self._desktop_controls
            self.status_badge = RoundedFrame(title_frame, bg=self._tint(self.accent_green),
                                              radius=14, border_color=self.accent_green,
                                              height=30, width=100, pad_x=8, pad_y=4)
            self.status_badge.grid(row=0, column=1, sticky="e")
            self.status_badge.grid_propagate(False)
            self.status_badge_label = tk.Label(
                self.status_badge.inner, text="PARTIAL" if sensor_status["disabled"] else "NO ALERTS",
                font=(FONT_STYLE, 11, "bold"),
                bg=self._tint(self.accent_green), fg=self.accent_green
            )
            self.status_badge_label.pack(expand=True)
            if sensor_status["allDisabled"]:
                self.status_badge.grid_remove()

        if subtitle:
            sub_label = tk.Label(
                title_frame,
                text=subtitle,
                font=(FONT_STYLE, 10),
                bg=self.panel_color,
                fg=self.text_sub,
                anchor="w"
            )
            sub_label.grid(row=1, column=0, sticky="w", pady=(3, 0))

        # give panel a row for content below the title (row=1) that callers use
        panel.grid_rowconfigure(1, weight=1)
        return panel

    def _create_value_card(self, parent, key, row, column, title, icon, initial_value, initial_status, accent):
        card = RoundedFrame(parent, bg=self.card_color, radius=14,
                             border_color=self.border_color, left_accent=accent)
        card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)
        inner = card.inner
        inner.grid_rowconfigure(2, weight=1)
        inner.grid_columnconfigure(0, weight=1)

        top = tk.Frame(inner, bg=self.card_color)
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 6))
        top.grid_columnconfigure(1, weight=1)

        badge_width = max(32, 18 * len(icon) + 20)
        icon_badge = RoundedFrame(top, bg=self._tint(accent), radius=9, height=28, width=badge_width,
                                   pad_x=6, pad_y=3)
        icon_badge.grid(row=0, column=0, sticky="w", padx=(0, 10))
        icon_badge.grid_propagate(False)
        tk.Label(icon_badge.inner, text=icon, font=(FONT_STYLE, 10, "bold"),
                 bg=self._tint(accent), fg=accent).pack(expand=True)

        title_label = tk.Label(
            top,
            text=title,
            font=(FONT_STYLE, 12, "bold"),
            bg=self.card_color,
            fg=self.text_main,
            anchor="w"
        )
        title_label.grid(row=0, column=1, sticky="w")

        value_label = tk.Label(
            inner,
            text=initial_value,
            font=(FONT_STYLE, self.value_font_size, "bold"),
            bg=self.card_color,
            fg=self.text_main,
            anchor="w",
            justify="left",
            wraplength=self.value_wrap_length
        )
        value_label.grid(row=1, column=0, sticky="ew", padx=4, pady=(8, 2))

        status_label = tk.Label(
            inner,
            text=initial_status,
            font=(FONT_STYLE, self.value_status_font_size),
            bg=self.card_color,
            fg=self._subcomment_color(),
            anchor="w",
            justify="left",
            wraplength=self.value_wrap_length
        )
        status_label.grid(row=2, column=0, sticky="new", padx=4, pady=(3, 8))

        pill = RoundedFrame(inner, bg=self._tint(accent), radius=12,
                             border_color=accent, height=26, width=90, pad_x=6, pad_y=3)
        pill.grid(row=3, column=0, sticky="w", padx=4, pady=(2, 4))
        pill.grid_propagate(False)
        pill_label = tk.Label(pill.inner, text="\u24d8 Waiting", font=(FONT_STYLE, 9, "bold"),
                               bg=self._tint(accent), fg=accent)
        pill_label.pack(expand=True)

        self.value_cards[key] = card
        self.value_labels[key] = value_label
        self.status_pills[key] = (pill, accent)
        self.status_pill_labels[key] = pill_label
        # keep a reference so status text updates can also swap the small
        # description text under the value
        self.value_cards[key + "_status_label"] = status_label

    def _create_status_legend(self, parent):
        """Show the four possible monitoring states so users know what to expect."""
        legend = tk.Frame(parent, bg=self.panel_color)
        legend.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 10))

        items = [
            ("Normal", "Within the safe range", self.accent_green),
            ("Warning", "Needs attention", self.accent_warning),
            ("Critical", "Needs immediate action", self.accent_danger),
            ("Prediction", "Trend may reach a limit", self.accent_prediction),
        ]

        for col in range(4):
            legend.grid_columnconfigure(col, weight=1, uniform="statuslegend")

        for col, (name, description, color) in enumerate(items):
            card = tk.Frame(
                legend,
                bg=self._tint(color),
                highlightthickness=1,
                highlightbackground=color,
                padx=8,
                pady=6
            )
            card.grid(row=0, column=col, sticky="nsew", padx=3)

            title_color = "#FFFFFF" if self.dark_mode else color
            desc_color = "#FFFFFF" if self.dark_mode else self.text_sub

            tk.Label(
                card,
                text=name.upper(),
                font=(FONT_STYLE, 9, "bold"),
                bg=self._tint(color),
                fg=title_color,
                anchor="w"
            ).pack(anchor="w")

            tk.Label(
                card,
                text=description,
                font=(FONT_STYLE, 8),
                bg=self._tint(color),
                fg=desc_color,
                anchor="w",
                justify="left",
                wraplength=max(105, int(self.alert_wrap_length / 4) - 18)
            ).pack(anchor="w", pady=(2, 0))

    def _create_empty_alert(self):
        self.empty_alert_card = RoundedFrame(
            self.alert_list, bg=self.card_soft, radius=16, border_color=self.border_color
        )
        self.empty_alert_card.grid(row=0, column=0, rowspan=config.MAX_TK_ALERTS, sticky="nsew")
        inner = self.empty_alert_card.inner
        inner.grid_columnconfigure(0, weight=1)
        inner.grid_rowconfigure(0, weight=1)

        empty_center = tk.Frame(inner, bg=self.card_soft)
        empty_center.grid(row=0, column=0)

        check_canvas = tk.Canvas(empty_center, width=90, height=90,
                                  bg=self.card_soft, highlightthickness=0)
        check_canvas.pack(pady=(0, 12))
        check_canvas.create_oval(4, 4, 86, 86, fill=self._tint(self.accent_green),
                                  outline=self._tint(self.accent_green))
        check_canvas.create_line(28, 46, 40, 60, width=5, fill=self.accent_green,
                                  capstyle="round")
        check_canvas.create_line(40, 60, 64, 30, width=5, fill=self.accent_green,
                                  capstyle="round")

        empty_title = tk.Label(
            empty_center,
            text="No active sensor alerts",
            font=(FONT_STYLE, 18, "bold"),
            bg=self.card_soft,
            fg=self.text_main,
            wraplength=self.alert_wrap_length,
            justify="center"
        )
        empty_title.pack(pady=(0, 4))

        empty_sub = tk.Label(
            empty_center,
            text="No active limit warnings or trend predictions right now.",
            font=(FONT_STYLE, 12),
            bg=self.card_soft,
            fg=self.text_sub,
            wraplength=self.alert_wrap_length,
            justify="center"
        )
        empty_sub.pack(pady=(0, 14))

    def _create_alert_card(self, parent, index):
        frame = RoundedFrame(parent, bg=self.card_color, radius=14,
                              border_color=self.border_color,
                              left_accent=self.accent_warning)
        frame.grid(row=index, column=0, sticky="nsew", pady=(0, 7))
        frame.grid_remove()
        inner = frame.inner
        inner.grid_columnconfigure(0, weight=1)
        inner.grid_columnconfigure(1, weight=0)

        title_label = tk.Label(
            inner,
            text="Sensor Alert",
            font=(FONT_STYLE, self.alert_title_font_size, "bold"),
            bg=self.card_color,
            fg=self.text_main,
            anchor="w",
            justify="left",
            # Reserve room for the WARNING / CRITICAL badge so long titles such
            # as "Water Level: OVERFLOWING" never disappear underneath it.
            wraplength=max(150, self.alert_wrap_length - 105)
        )
        title_label.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 2))

        severity_badge = RoundedFrame(inner, bg=self._tint(self.accent_warning),
                                      radius=10, border_color=self.accent_warning,
                                      height=22, width=76, pad_x=5, pad_y=2)
        severity_badge.grid(row=0, column=1, sticky="ne", padx=4, pady=(5, 2))
        severity_badge.grid_propagate(False)
        severity_label = tk.Label(severity_badge.inner, text="WARNING",
                                  font=(FONT_STYLE, 8, "bold"),
                                  bg=self._tint(self.accent_warning), fg=self.accent_warning)
        severity_label.pack(expand=True)

        message_label = tk.Label(
            inner,
            text="",
            font=(FONT_STYLE, self.alert_message_font_size),
            bg=self.card_color,
            fg=self.text_sub,
            anchor="nw",
            justify="left",
            wraplength=self.alert_wrap_length
        )
        message_label.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 2))

        meta_label = tk.Label(
            inner,
            text="",
            font=(FONT_STYLE, self.alert_meta_font_size, "bold"),
            bg=self.card_color,
            fg=self.text_sub,
            anchor="w",
            justify="left",
            wraplength=self.alert_wrap_length
        )
        meta_label.grid(row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=(3, 8))

        return {
            "frame": frame,
            "title": title_label,
            "message": message_label,
            "meta": meta_label,
            "severity_badge": severity_badge,
            "severity_label": severity_label
        }

    def _tick_clock(self):
        self.clock_label.config(text=time.strftime("%I:%M:%S %p"))
        controls = self._desktop_controls
        if controls['allDisabled']:
            self.footer_label.config(text="All sensors disabled • Sensor protections bypassed • Feeding and manual controls available", fg=self.text_sub)
        elif controls['disabled']:
            labels = ', '.join(controls['sensors'][k]['label'] for k in controls['disabled'])
            self.footer_label.config(text="Disabled: " + labels + " • Controls & settings → Sensors", fg=self.text_sub)
        else:
            self.footer_label.config(text="Controls & settings for manual actions • Esc exits full screen", fg=self.text_sub)
        self.root.after(1000, self._tick_clock)

    # ------------------------------------------------------------------
    # Data updates
    # ------------------------------------------------------------------
    def update_labels(self, temp, float_stat, turbidity, ph, alerts=None):
        """Accept a sensor update from any thread without touching Tkinter."""
        self._snapshot_worker.request()

    def _process_pending_updates(self):
        """Only perform widget updates here; snapshot work runs off the Tk thread."""
        if self._settings_version_seen != config.settings_version:
            self._settings_version_seen = config.settings_version
            self._snapshot_worker.request()
        try:
            version, snapshot, controls, alerts = self._pending_updates.get_nowait()
        except queue.Empty:
            pass
        else:
            if version == config.settings_version:
                self._desktop_controls = controls
                self._apply_updates(snapshot['temp'], snapshot['float_stat'],
                                    snapshot['turbidity'], snapshot['ph'], alerts)
            else:
                self._snapshot_worker.request()
        self.root.after(100, self._process_pending_updates)

    def _apply_updates(self, temp, float_stat, turbidity, ph, alerts):
        self._last_update = (temp, float_stat, turbidity, ph, alerts)
        self._set_value_card(
            "temp",
            self._format_temp_display(temp),
            self._temperature_status(temp),
            self._temperature_color(temp)
        )
        self._set_value_card(
            "water",
            self._format_water_display(float_stat),
            self._water_status(float_stat),
            self._water_color(float_stat)
        )
        self._set_value_card(
            "turbidity",
            self._format_plain_display(turbidity),
            self._turbidity_status(turbidity),
            self._turbidity_color(turbidity)
        )
        self._set_value_card(
            "ph",
            self._format_plain_display(ph),
            self._ph_status(ph),
            self._ph_color(ph)
        )
        self._render_alerts(alerts)

    def _set_value_card(self, key, value, status, color):
        # Keep long categorical values (especially water-level states) visually balanced.
        display_text = str(value) if value is not None else "--"
        if display_text.upper() in {'DISABLED', 'PARTIAL', 'WAITING', 'PAUSED'}:
            color = '#64748B'

        # Avoid repeatedly reconfiguring the same Tk widgets when a sensor
        # packet contains values that are already on screen.
        state = (
            display_text,
            status,
            color,
            self.dark_mode,
        )

        if self._last_card_state.get(key) == state:
            return

        self._last_card_state[key] = state

        font_size = self.value_font_size
        if key == "water":
            # Water states have different lengths. Scale them individually so the
            # final character is never clipped on a compact Raspberry Pi display.
            if display_text.lower() == "overflowing":
                font_size = 16 if self.compact_screen else 20
            elif display_text.lower() == "very low":
                font_size = 18 if self.compact_screen else 22
            else:
                font_size = 20 if self.compact_screen else 24

        self.value_labels[key].config(
            text=display_text,
            font=(FONT_STYLE, font_size, "bold")
        )
        self.value_cards[key + "_status_label"].config(
            text=status,
            wraplength=self.value_wrap_length,
            justify="left"
        )

        pill, default_accent = self.status_pills[key]
        active_color = color if color != self.text_main else default_accent
        tint = self._tint(active_color)
        pill.itemconfig("bg", fill=tint, outline=active_color)
        pill._bg = tint
        pill._border_color = active_color
        pill.inner.config(bg=tint)
        label = self.status_pill_labels[key]
        # In dark mode the colored pill background stays unchanged, but the
        # status word itself is white for maximum contrast/readability.
        pill_text_color = "#FFFFFF" if self.dark_mode else active_color
        label.config(bg=tint, fg=pill_text_color)
        # Show a compact word for the pill (Waiting / Normal / Warning / Critical)
        label.config(text=self._pill_word(status))

    def _pill_word(self, status):
        text = status.lower()
        if "partial" in text:
            return "Partial"
        if "disabled" in text:
            return "Disabled"
        if "paused" in text:
            return "Paused"
        if "waiting" in text:
            return "\u24d8 Waiting"
        if "critical" in text:
            return "\u26a0 Critical"
        if "warning" in text or "above" in text or "below" in text or "off" in text:
            return "\u26a0 Warning"
        return "\u2713 Normal"

    def change_temperature_unit(self, event=None):
        self.set_temperature_unit('F' if self.temperature_unit_var.get().startswith('Fahrenheit') else 'C')

    def set_temperature_unit(self, unit):
        self.temperature_unit = 'F' if unit == 'F' else 'C'
        self.temperature_unit_var.set('Fahrenheit (°F)' if self.temperature_unit == 'F' else 'Celsius (°C)')
        config.set_db_value('gui_temperature_unit', self.temperature_unit)
        self._last_alert_signature = None
        if self._last_update is not None:
            self._apply_updates(*self._last_update)
        controls = getattr(self, 'controls_window', None)
        if controls is not None and controls.winfo_exists():
            controls.change_temperature_unit(self.temperature_unit)

    def _format_temp_display(self, value):
        return temperature_units.format_reading(value, self.temperature_unit)

    def _format_plain_display(self, value):
        if value is None:
            return "--"
        text = str(value).strip()
        return text if text else "--"

    def _format_water_display(self, value):
        """Use a short, consistent categorical label in the Water Level card."""
        text = str(format_float_status(value)).lower()
        if text in {'disabled', 'partial', 'waiting'}:
            return text.upper()
        if "overflow" in text:
            return "Overflowing"
        if "very low" in text:
            return "Very Low"
        if "normal" in text:
            return "Normal"
        if "low" in text:
            return "Low"
        if "detected" in text:
            return "DETECTED"
        return "--"

    def _temperature_status(self, value):
        if str(value).strip().upper() == "DISABLED":
            return "Disabled by operator; alerts and automatic actions bypassed."
        if str(value).strip().upper() == "PAUSED":
            return "Paused while a water pump is active."
        numeric = get_sensor_numeric("temp", value)
        if numeric is None:
            return "Waiting for the temperature sensor."
        if numeric >= config.TEMP_OVERHEAT_LIMIT_C:
            return "Critical: the water temperature is dangerously high."
        if numeric >= config.TEMP_LIMIT_C:
            return "Warning: the water is warmer than the safe setting."
        return "Temperature is within the safe setting."

    def _temperature_color(self, value):
        numeric = get_sensor_numeric("temp", value)
        if numeric is None:
            return self.text_main
        if numeric >= config.TEMP_OVERHEAT_LIMIT_C:
            return self.accent_danger
        if numeric >= config.TEMP_LIMIT_C:
            return self.accent_warning
        return self.accent_green

    def _water_status(self, value):
        floats = [self._desktop_controls.get('sensors', {}).get(key, {}).get('enabled', True)
                  for key in ('float_low', 'float_normal', 'float_upper')]
        if not all(floats):
            return 'Disabled: all float switches.' if not any(floats) else 'Partial monitoring; disabled float protections bypassed.'
        text = str(format_float_status(value)).lower()
        if "overflow" in text:
            return "Critical: water is overflowing."
        if "normal" in text:
            return "Water level is normal."
        if "very low" in text:
            return "Critical: water level is very low."
        if "low" in text:
            return "Warning: water level is low. Check if the tank needs water."
        if "detected" in text:
            return "Water level sensor is active."
        return "Waiting for the water-level sensor."

    def _water_color(self, value):
        text = str(format_float_status(value)).lower()
        if "overflow" in text:
            return self.accent_danger
        if "normal" in text:
            return self.accent_green
        if "very low" in text:
            return self.accent_danger
        if "low" in text:
            return self.accent_warning
        if "detected" in text:
            return self.accent_green
        return self.text_main

    def _turbidity_status(self, value):
        water = getattr(self, '_desktop_controls', {}).get('waterControl', {})
        if water.get('calibrationActive'):
            return 'Calibration active: automatic clarity correction paused.'
        if str(value).strip().upper() == "DISABLED":
            return "Disabled by operator; alerts and automatic actions bypassed."
        if str(value).strip().upper() == "PAUSED":
            return "Paused during insertion/manual pumping; floats remain active."
        numeric = get_sensor_numeric("turbidity", value)
        if numeric is None:
            return "Waiting for the turbidity sensor."
        if numeric >= config.TURBIDITY_CRITICAL_NTU:
            return "Critical: the water is much cloudier than the safe setting."
        if numeric >= config.TURBIDITY_LIMIT_NTU:
            return "Warning: the water is cloudier than the safe setting."
        return "Water clarity is within the safe setting."

    def _turbidity_color(self, value):
        numeric = get_sensor_numeric("turbidity", value)
        if numeric is None:
            return self.text_main
        if numeric >= config.TURBIDITY_CRITICAL_NTU:
            return self.accent_danger
        if numeric >= config.TURBIDITY_LIMIT_NTU:
            return self.accent_warning
        return self.accent_green

    def _ph_status(self, value):
        water = getattr(self, '_desktop_controls', {}).get('waterControl', {})
        if water.get('calibrationActive'):
            return 'Calibration active: automatic pH correction paused.'
        settling = water.get('phSettling', {})
        if settling.get('active'):
            if settling.get('waitingForNormal'):
                return 'pH correction waits until pumps stop and level is Normal.'
            return f"pH settling: {settling.get('remainingSeconds', 0)} seconds remaining."
        if str(value).strip().upper() == "DISABLED":
            return "Disabled by operator; alerts and automatic actions bypassed."
        if str(value).strip().upper() == "PAUSED":
            return "Paused while a water pump is active."
        numeric = get_sensor_numeric("ph", value)
        if numeric is None:
            return "Waiting for the pH sensor."
        if numeric >= config.PH_CRITICAL_HIGH_LIMIT:
            return "Critical: pH is much too high (too alkaline)."
        if numeric <= config.PH_CRITICAL_LOW_LIMIT:
            return "Critical: pH is much too low (too acidic)."
        if numeric >= config.PH_HIGH_LIMIT:
            return "Warning: pH is too high (too alkaline)."
        if numeric <= config.PH_LOW_LIMIT:
            return "Warning: pH is too low (too acidic)."
        return "pH is within the safe range."

    def _ph_color(self, value):
        numeric = get_sensor_numeric("ph", value)
        if numeric is None:
            return self.text_main
        if numeric >= config.PH_CRITICAL_HIGH_LIMIT or numeric <= config.PH_CRITICAL_LOW_LIMIT:
            return self.accent_danger
        if numeric >= config.PH_HIGH_LIMIT or numeric <= config.PH_LOW_LIMIT:
            return self.accent_warning
        return self.accent_green

    def _render_alerts(self, alerts):
        # Alert cards are comparatively expensive Canvas/Tk widgets. Redraw
        # them only when their visible content or the active theme changes.
        alert_signature = (
            self.dark_mode,
            tuple(self._desktop_controls['disabled']),
            tuple(
                (
                    alert.get("sensor"),
                    alert.get("type"),
                    alert.get("severity"),
                    alert.get("trend"),
                    alert.get("title"),
                    alert.get("short_message"),
                    alert.get("message"),
                    str(alert.get("value")),
                    str(alert.get("limit")),
                    round(float(alert.get("eta_seconds", 0) or 0), 1),
                )
                for alert in (alerts or [])
            ),
        )

        if alert_signature == self._last_alert_signature:
            return

        self._last_alert_signature = alert_signature

        if not alerts:
            sensor_status = self._desktop_controls
            if sensor_status["allDisabled"]:
                self.status_badge.grid_remove()
            else:
                self.status_badge.grid()
            self.status_badge_label.config(
                text="PARTIAL" if sensor_status["disabled"] else "NO ALERTS",
                bg=self._tint(self.accent_green),
                fg="#FFFFFF" if self.dark_mode else self.accent_green
            )
            self.status_badge.itemconfig("bg", fill=self._tint(self.accent_green),
                                          outline=self.accent_green)
            self.status_badge.inner.config(bg=self._tint(self.accent_green))
            self.empty_alert_card.grid()
            for card in self.alert_cards:
                card["frame"].grid_remove()
                card["visible"] = False
            return

        ordered_alerts = self._prioritize_alerts(alerts)
        critical_count = sum(1 for alert in ordered_alerts if alert.get("severity") == "critical")
        prediction_count = sum(1 for alert in ordered_alerts if alert.get("severity") == "prediction")
        summary_color = (
            self.accent_danger
            if critical_count
            else self.accent_prediction
            if prediction_count
            else self.accent_warning
        )
        summary_text = (
            f"{critical_count} CRITICAL"
            if critical_count
            else f"{prediction_count} PRED"
            if prediction_count
            else f"{len(ordered_alerts)} ACTIVE"
        )
        tint = self._tint(summary_color)
        self.status_badge.grid()
        self.status_badge_label.config(
            text=summary_text,
            bg=tint,
            fg="#FFFFFF" if self.dark_mode else summary_color
        )
        self.status_badge.itemconfig("bg", fill=tint, outline=summary_color)
        self.status_badge.inner.config(bg=tint)
        self.empty_alert_card.grid_remove()

        for index, card in enumerate(self.alert_cards):
            if index >= len(ordered_alerts):
                card["frame"].grid_remove()
                card["visible"] = False
                continue

            alert = ordered_alerts[index]
            severity = alert.get("severity", "warning")
            color = self._severity_color(severity)
            title = self._alert_title(alert)
            message = temperature_units.convert_text(self._alert_message(alert), self.temperature_unit)

            if index == config.MAX_TK_ALERTS - 1 and len(ordered_alerts) > config.MAX_TK_ALERTS:
                remaining = len(ordered_alerts) - config.MAX_TK_ALERTS + 1
                title = f"+{remaining} more notification(s)"
                message = "Prediction alerts are shown first. Open the web dashboard for all details."

            meta = self._alert_meta(alert)
            visible_state = (title, message, meta, severity, self.dark_mode)
            if card.get('rendered') == visible_state and card.get('visible'):
                continue
            card['rendered'] = visible_state
            card['visible'] = True
            card["frame"]._left_accent = color
            card["frame"]._redraw()
            # Dark mode keeps the alert/prediction backgrounds and accent border,
            # while all important text becomes white so sensor names, prediction
            # descriptions and water-level warnings remain easy to read.
            alert_title_color = "#FFFFFF" if self.dark_mode else color
            alert_body_color = "#FFFFFF" if self.dark_mode else self.text_sub
            alert_meta_color = "#FFFFFF" if self.dark_mode else (color if meta else self.text_sub)
            card["title"].config(text=title, fg=alert_title_color)
            card["message"].config(text=message, fg=alert_body_color)
            card["meta"].config(text=meta, fg=alert_meta_color)
            badge_tint = self._tint(color)
            card["severity_badge"]._bg = badge_tint
            card["severity_badge"]._border_color = color
            card["severity_badge"].itemconfig("bg", fill=badge_tint, outline=color)
            card["severity_badge"].inner.config(bg=badge_tint)
            card["severity_label"].config(
                text=str(severity).upper(),
                bg=badge_tint,
                fg="#FFFFFF" if self.dark_mode else color
            )
            card["frame"].grid()

    def _severity_color(self, severity):
        if severity == "critical":
            return self.accent_danger
        if severity == "prediction":
            return self.accent_prediction
        if severity == "warning":
            return self.accent_warning
        return self.accent_green

    def _prioritize_alerts(self, alerts):
        def priority(alert):
            severity = alert.get("severity", "warning")
            alert_type = alert.get("type", "")
            sensor = str(alert.get("sensor", "")).lower()

            # Never let an active water-level warning be pushed off the visible
            # notification list by several trend predictions.
            if severity == "critical":
                return 0
            if "water level" in sensor and severity == "warning":
                return 1
            if severity == "prediction" or alert_type == "prediction":
                return 2
            if severity == "warning":
                return 3
            return 4

        return sorted(alerts, key=priority)

    def _alert_title(self, alert):
        if alert.get("severity") == "prediction" or alert.get("type") == "prediction":
            return "PREDICTION: " + str(alert.get("sensor") or "Sensor")
        return alert.get("title") or alert.get("sensor") or "Sensor Alert"

    def _alert_message(self, alert):
        if alert.get("severity") == "prediction" or alert.get("type") == "prediction":
            return alert.get("short_message") or alert.get("message") or "Prediction alert needs attention."
        return alert.get("message") or "Sensor value needs attention."

    def _alert_meta(self, alert):
        parts = []
        value = alert.get("value")
        limit = alert.get("limit")
        unit = alert.get("unit", "")
        if unit == '°C':
            if isinstance(value, (int, float)): value = temperature_units.to_display(value, self.temperature_unit)
            if isinstance(limit, (int, float)): limit = temperature_units.to_display(limit, self.temperature_unit)
            unit = '°' + self.temperature_unit

        if isinstance(value, (int, float)):
            parts.append(f"Value: {value:g}{unit}")
        if isinstance(limit, (int, float)):
            parts.append(f"Limit/target: {limit:g}{unit}")
        if alert.get("trend"):
            parts.append(f"Trend: {alert.get('trend')}")
        lasts_seconds = alert.get("lasts_seconds")
        if isinstance(lasts_seconds, (int, float)):
            parts.append(f"Lasts: {format_duration(lasts_seconds)}")

        return "   •   ".join(parts)
