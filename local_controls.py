"""Local controls with asynchronous status and command handling."""
import tkinter as tk
from tkinter import ttk
import webbrowser
import threading
import queue
import config
import temperature_units
import feeding_control
import water_control
import sensor_control
import fan_control
import admin_credentials
from gui_snapshot import SnapshotWorker

LIMIT_FIELDS = (
    ('tempLimit', 'Temperature warning (°C)', ('temperature', 'limit')),
    ('tempCriticalLimit', 'Temperature critical (°C)', ('temperature', 'overheatLimit')),
    ('phLowLimit', 'Lower', ('ph', 'lowLimit')),
    ('phHighLimit', 'Upper', ('ph', 'highLimit')),
    ('phCriticalLowLimit', 'Lower', ('ph', 'criticalLowLimit')),
    ('phCriticalHighLimit', 'Upper', ('ph', 'criticalHighLimit')),
    ('predictionDelaySeconds', 'Prediction history (seconds)', ('predictionDelaySeconds',)),
    ('predictionAlertDurationSeconds', 'Prediction display time (seconds)', ('predictionAlertDurationSeconds',)),
    ('maxWebsiteViewers', 'Maximum dashboard viewers', ('maxWebsiteViewers',)),
    ('maxCameraViewers', 'Maximum camera viewers', ('maxCameraViewers',)),
    ('maxAdminSessions', 'Maximum signed-in Admin devices', ('maxAdminSessions',)),
)


def style_controls(root, theme):
    style = ttk.Style(root)
    style.configure('Aqua.TFrame', background=theme['bg'])
    style.configure('Aqua.Card.TFrame', background=theme['panel'])
    style.configure('Aqua.TLabel', background=theme['panel'], foreground=theme['text_main'], font=('DejaVu Sans', 10))
    style.configure('Aqua.Title.TLabel', background=theme['bg'], foreground=theme['text_main'], font=('DejaVu Sans', 19, 'bold'))
    style.configure('Aqua.Heading.TLabel', background=theme['panel'], foreground=theme['text_main'], font=('DejaVu Sans', 15, 'bold'))
    style.configure('Aqua.Status.TLabel', background=theme['info_box_bg'], foreground=theme['text_main'], padding=12, font=('DejaVu Sans', 10))
    style.configure('Aqua.Message.TLabel', background=theme['bg'], foreground=theme['text_main'], font=('DejaVu Sans', 10))
    style.configure('Aqua.TButton', padding=(12, 9), font=('DejaVu Sans', 10), background=theme['card_soft'], foreground=theme['text_main'])
    style.map('Aqua.TButton', background=[('active', theme['border'])], foreground=[('disabled', '#8494a2')])
    style.configure('Aqua.Primary.TButton', padding=(12, 9), font=('DejaVu Sans', 10), background='#126b80', foreground='white')
    style.map('Aqua.Primary.TButton', background=[('disabled', '#a5bac0'), ('active', '#0b5264')], foreground=[('disabled', 'white'), ('!disabled', 'white')])
    style.configure('Aqua.Danger.TButton', padding=(12, 9), font=('DejaVu Sans', 10), background='#b73542', foreground='white')
    style.map('Aqua.Danger.TButton', background=[('disabled', '#c6a5a8'), ('active', '#922832')], foreground=[('!disabled', 'white')])
    style.configure('Aqua.Nav.TButton', padding=(12, 11), anchor='w', font=('DejaVu Sans', 10), background=theme['bg'], foreground=theme['text_sub'])
    style.map('Aqua.Nav.TButton', background=[('pressed', '#126b80'), ('active', theme['border'])], foreground=[('pressed', 'white')])
    style.configure('Aqua.TEntry', padding=8, font=('DejaVu Sans', 11))
    style.configure('Aqua.TLabelframe', background=theme['panel'], padding=12, borderwidth=1, relief='solid', bordercolor=theme['border'], lightcolor=theme['border'], darkcolor=theme['border'])
    style.configure('Aqua.TLabelframe.Label', background=theme['panel'], foreground=theme['text_main'], font=('DejaVu Sans', 10, 'bold'))


class LocalControls(tk.Toplevel):
    def __init__(self, parent, schedule_callback, on_temperature_unit_change, water_schedule_callback):
        super().__init__(parent)
        self.temperature_unit = 'F' if config.get_db_value('gui_temperature_unit', 'C') == 'F' else 'C'
        self.on_temperature_unit_change = on_temperature_unit_change
        self.title('AquaPi • Controls & settings')
        self.geometry(f'{min(860, self.winfo_screenwidth()-40)}x{min(610, self.winfo_screenheight()-60)}')
        self.minsize(620, 400)
        self.transient(parent)
        self.configure(bg=ttk.Style(self).lookup('Aqua.TFrame', 'background'))
        self.busy = False
        self.results = queue.Queue(maxsize=1)
        self._last_status = None
        self.after_id = None
        self.command_buttons = []
        self.sensor_rows = {}
        self.limit_vars = {}
        self.limit_labels = {}
        self.message = tk.StringVar()
        heading = ttk.Frame(self, style='Aqua.TFrame', padding=(22, 18, 22, 12))
        heading.pack(fill='x')
        ttk.Label(heading, text='Controls & settings', style='Aqua.Title.TLabel').pack(anchor='w')
        body = ttk.Frame(self, style='Aqua.TFrame', padding=(16, 0, 16, 0))
        body.pack(fill='both', expand=True)
        sidebar = ttk.Frame(body, style='Aqua.TFrame')
        sidebar.pack(side='left', fill='y', padx=(0, 16))
        content = ttk.Frame(body, style='Aqua.Card.TFrame')
        content.pack(side='left', fill='both', expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self.tabs, self.pages, self.navigation, self.canvases = {}, {}, {}, {}
        sections = [('Feeding', 'Feeding'), ('Water pumps', 'Water Changes'), ('Fan', 'Cooling Fan'),
                    ('Camera', 'Camera'), ('Alert settings', 'System settings'), ('Admin PIN', 'Admin PIN')]
        if config.DEVELOPER_OPTIONS:
            sections.append(('Sensors', 'Sensors'))
        for key, label in sections:
            button = ttk.Button(sidebar, text=label, style='Aqua.Nav.TButton',
                                command=lambda name=key: self.show_page(name), width=16)
            button.pack(fill='x', pady=(0, 6))
            self.navigation[key] = button
            page = ttk.Frame(content, style='Aqua.Card.TFrame')
            page.grid(row=0, column=0, sticky='nsew')
            canvas = tk.Canvas(page, bg=ttk.Style(self).lookup('Aqua.Card.TFrame', 'background'), highlightthickness=0, width=400)
            scrollbar = ttk.Scrollbar(page, orient='vertical', command=canvas.yview)
            canvas.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side='right', fill='y')
            canvas.pack(fill='both', expand=True)
            frame = ttk.Frame(canvas, style='Aqua.Card.TFrame', padding=20)
            window = canvas.create_window((0, 0), window=frame, anchor='nw')
            frame.bind('<Configure>', lambda event, c=canvas: c.configure(scrollregion=c.bbox('all')))
            canvas.bind('<Configure>', lambda event, c=canvas, i=window: c.itemconfigure(i, width=event.width))
            self.pages[key], self.tabs[key], self.canvases[key] = page, frame, canvas
        self.bind('<MouseWheel>', self._scroll)
        self.bind('<Button-4>', lambda event: self._scroll(event, -1))
        self.bind('<Button-5>', lambda event: self._scroll(event, 1))
        self._build_feeding(schedule_callback)
        self._build_pumps(water_schedule_callback)
        self._build_fan()
        self._build_camera()
        self._build_limits()
        self._build_pin()
        if config.DEVELOPER_OPTIONS:
            self._build_sensors()
        ttk.Label(self, textvariable=self.message, style='Aqua.Message.TLabel',
                  wraplength=720, padding=(22, 10)).pack(fill='x')
        self.worker = SnapshotWorker(self._read_status)
        self.show_page('Feeding')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda event: self.close())
        self.refresh()

    def _title(self, parent, text):
        ttk.Label(parent, text=text, style='Aqua.Heading.TLabel').pack(anchor='w', pady=(0, 16))

    def _button(self, parent, text, command, style='Aqua.TButton'):
        button = ttk.Button(parent, text=text, command=command, style=style)
        self.command_buttons.append(button)
        return button

    def _status(self, parent):
        value = tk.StringVar(value='Connecting…')
        ttk.Label(parent, textvariable=value, style='Aqua.Status.TLabel', wraplength=430).pack(fill='x', pady=(0, 16))
        return value

    def _build_feeding(self, schedule_callback):
        panel = self.tabs['Feeding']
        self._title(panel, 'Feeding')
        self.feed_status = self._status(panel)
        form = ttk.Frame(panel, style='Aqua.Card.TFrame')
        form.pack(fill='x', pady=(0, 16))
        ttk.Label(form, text='Cycles per serving', style='Aqua.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 6))
        self.cycles = tk.StringVar(value='1')
        ttk.Entry(form, textvariable=self.cycles, width=8, style='Aqua.TEntry').grid(row=1, column=0, sticky='w')
        self.feed_button = self._button(form, 'Feed now', self.feed_now, 'Aqua.Primary.TButton')
        self.feed_button.grid(row=1, column=1, padx=10)
        self._button(form, 'Stop feeder', lambda: self.run(feeding_control.stop_feeder), 'Aqua.Danger.TButton').grid(row=1, column=2)
        row = ttk.Frame(panel, style='Aqua.Card.TFrame'); row.pack(fill='x', pady=(0, 16))
        self._button(row, 'Pause schedule', lambda: self.run(lambda: feeding_control.set_paused(True))).pack(side='left', padx=(0, 8))
        self._button(row, 'Resume schedule', lambda: self.run(lambda: feeding_control.set_paused(False))).pack(side='left')
        ttk.Button(panel, text='Feeding schedule', style='Aqua.Primary.TButton', command=schedule_callback).pack(anchor='w')

    def _build_pumps(self, water_schedule_callback):
        panel = self.tabs['Water pumps']
        self._title(panel, 'Water Changes')
        self.pump_status = self._status(panel)
        grid = ttk.Frame(panel, style='Aqua.Card.TFrame'); grid.pack(fill='x')
        grid.columnconfigure((0, 1), weight=1)
        actions = [('Drain water', 'extract_start', 'Aqua.Primary.TButton'),
                   ('Refill water', 'insert_start', 'Aqua.Primary.TButton'),
                   ('Stop draining', 'extract_stop', 'Aqua.TButton'),
                   ('Stop refilling', 'insert_stop', 'Aqua.TButton'),
                   ('Drain + refill', 'both_start', 'Aqua.TButton'),
                   ('Stop all pumps', 'stop_all', 'Aqua.Danger.TButton')]
        for i, (label, action, style) in enumerate(actions):
            self._button(grid, label, lambda a=action: self.run(lambda: water_control.manual_action(a)), style).grid(row=i//2, column=i%2, sticky='ew', padx=4, pady=5)
        self._button(panel, 'Resume automatic control', lambda: self.run(lambda: water_control.manual_action('auto_resume'))).pack(fill='x', padx=4, pady=(8, 18))
        ttk.Separator(panel).pack(fill='x', pady=(0, 18))
        ttk.Button(panel, text='Water-change schedule', style='Aqua.Primary.TButton', command=water_schedule_callback).pack(anchor='w')

    def _build_fan(self):
        panel = self.tabs['Fan']; self._title(panel, 'Cooling Fan')
        self.fan_status = self._status(panel)
        row = ttk.Frame(panel, style='Aqua.Card.TFrame'); row.pack(fill='x')
        self.fan_buttons = []
        for label, mode in [('Auto', 'AUTO'), ('Fan on', 'ON'), ('Fan off', 'OFF')]:
            button = self._button(row, label, lambda m=mode: self.run(lambda: fan_control.set_mode(m)))
            button.pack(side='left', padx=(0, 10)); self.fan_buttons.append(button)

    def _build_sensors(self):
        panel = self.tabs['Sensors']; self._title(panel, 'Sensors')
        self.sensor_sync = self._status(panel)
        for key, (label, _) in sensor_control.SENSORS.items():
            row = ttk.Frame(panel, style='Aqua.Card.TFrame'); row.pack(fill='x', pady=(0, 14))
            row.columnconfigure(0, weight=1)
            ttk.Label(row, text=label, style='Aqua.TLabel').grid(row=0, column=0, sticky='w')
            status = tk.StringVar()
            ttk.Label(row, textvariable=status, style='Aqua.TLabel').grid(row=1, column=0, sticky='w', pady=(4, 0))
            button = self._button(row, 'Enable', lambda k=key: self.run(lambda: sensor_control.apply({k: not sensor_control.enabled(k)})))
            button.grid(row=0, column=1, rowspan=2, padx=(12, 0))
            self.sensor_rows[key] = (status, button)

    def _build_camera(self):
        panel = self.tabs['Camera']; self._title(panel, 'Camera')
        arrows = ttk.Frame(panel, style='Aqua.Card.TFrame'); arrows.pack(pady=(0, 20))
        for name, r, c in [('up', 0, 1), ('left', 1, 0), ('center', 1, 1), ('right', 1, 2), ('down', 2, 1)]:
            self._button(arrows, name.title(), lambda cmd=name: self.run(lambda: self.camera_command(cmd))).grid(row=r, column=c, padx=4, pady=4)
        ttk.Button(panel, text='Open camera view', style='Aqua.Primary.TButton', command=lambda: webbrowser.open(f'http://localhost:{config.WEB_PORT}/camera')).pack(anchor='w')

    def _build_limits(self):
        panel = self.tabs['Alert settings']; self._title(panel, 'System settings')
        values = config.get_public_limits()
        groups = [('Temperature', LIMIT_FIELDS[:2]), ('pH · Warning range', LIMIT_FIELDS[2:4]),
                  ('pH · Critical limits', LIMIT_FIELDS[4:6]), ('Predictions', LIMIT_FIELDS[6:8]),
                  ('Access limits', LIMIT_FIELDS[8:])]
        for title, fields in groups:
            group = ttk.LabelFrame(panel, text=title, style='Aqua.TLabelframe')
            group.pack(fill='x', pady=(0, 14)); group.columnconfigure((0, 1), weight=1)
            for i, (key, label, path) in enumerate(fields):
                value = values
                for part in path: value = value[part]
                if key in ('tempLimit', 'tempCriticalLimit'):
                    value = temperature_units.to_display(value, self.temperature_unit)
                    label = label.replace('°C', '°' + self.temperature_unit)
                var = tk.StringVar(value=f'{value:g}'); self.limit_vars[key] = var
                field = ttk.Frame(group, style='Aqua.Card.TFrame')
                field.grid(row=i//2, column=i%2, sticky='ew', padx=5, pady=5)
                self.limit_labels[key] = ttk.Label(field, text=label, style='Aqua.TLabel', wraplength=175)
                self.limit_labels[key].pack(anchor='w', pady=(0, 6))
                ttk.Entry(field, textvariable=var, width=16, style='Aqua.TEntry').pack(fill='x')
        self._button(panel, 'Save settings', self.submit_limits, 'Aqua.Primary.TButton').pack(anchor='w')

    def _build_pin(self):
        panel = self.tabs['Admin PIN']; self._title(panel, 'Admin PIN')
        self.pin_vars = [tk.StringVar() for _ in range(3)]
        for label, var in zip(('Current PIN', 'New PIN', 'Confirm new PIN'), self.pin_vars):
            ttk.Label(panel, text=label, style='Aqua.TLabel').pack(anchor='w', pady=(0, 5))
            ttk.Entry(panel, textvariable=var, show='•', style='Aqua.TEntry', width=28).pack(anchor='w', pady=(0, 14))
        self.pin_save_button = self._button(panel, 'Save PIN', self.change_admin_pin, 'Aqua.Primary.TButton')
        self.pin_save_button.pack(anchor='w')

    def show_page(self, name):
        self.pages[name].tkraise()
        self.active_page = name
        for key, button in self.navigation.items():
            button.state(['pressed'] if key == name else ['!pressed'])
        self.message.set('')

    def _scroll(self, event, direction=None):
        canvas = self.canvases[self.active_page]
        bounds = canvas.bbox('all')
        if bounds and bounds[3] > canvas.winfo_height():
            canvas.yview_scroll(direction or (-1 if event.delta > 0 else 1), 'units')

    def camera_command(self, command):
        if not config.send_to_identified_output_serial_ports(config.build_camera_serial_message(command)):
            raise ValueError('Camera disconnected.')
        config.update_servo_position_for_command(command)

    def feed_now(self):
        cycles = self.cycles.get()
        self.run(lambda: feeding_control.request_feed(cycles))

    def change_admin_pin(self):
        values = [var.get() for var in self.pin_vars]
        self.run(lambda: admin_credentials.change(*values), 'PIN changed. Sign in again on the web.', clear_pin=True)

    def change_temperature_unit(self, unit):
        previous = self.temperature_unit
        self.temperature_unit = unit
        for key in ('tempLimit', 'tempCriticalLimit'):
            try:
                value = temperature_units.to_celsius(self.limit_vars[key].get(), previous)
                self.limit_vars[key].set(f'{temperature_units.to_display(value, unit):g}')
            except ValueError:
                pass
            label = self.limit_labels[key]
            label.configure(text=label.cget('text').replace('°' + previous, '°' + unit))
        if self._last_status:
            self._render_status(self._last_status)

    def save_limits(self):
        # Also used directly by callers with an already prepared form.
        payload = {key: var.get() for key, var in self.limit_vars.items()}
        for key in ('tempLimit', 'tempCriticalLimit'):
            payload[key] = temperature_units.to_celsius(payload[key], self.temperature_unit)
        return config.update_limits_from_payload(payload)

    def submit_limits(self):
        try:
            payload = {key: var.get() for key, var in self.limit_vars.items()}
            for key in ('tempLimit', 'tempCriticalLimit'):
                payload[key] = temperature_units.to_celsius(payload[key], self.temperature_unit)
        except ValueError:
            self.message.set('Enter valid temperature limits.')
            return
        self.run(lambda: config.update_limits_from_payload(payload), 'Settings saved.')

    def run(self, action, success='Done.', clear_pin=False):
        if self.busy:
            return
        self.busy = True
        self.message.set('Sending…')
        for button in self.command_buttons: button.state(['disabled'])
        def work():
            try:
                action()
                self.results.put((True, success, clear_pin))
            except Exception as exc:
                self.results.put((False, str(exc), False))
        threading.Thread(target=work, name='AquaPi local command', daemon=True).start()

    @staticmethod
    def _read_status():
        return dict(fan=fan_control.get_status(), sensors=sensor_control.get_status(),
                    feeder=feeding_control.get_status(), pumps=water_control.get_status())

    def _render_status(self, snapshot):
        fan, controls, state, pumps = (snapshot[key] for key in ('fan', 'sensors', 'feeder', 'pumps'))
        text = 'Fan disconnected'
        if fan['available']:
            text = fan['mode'] + ' · Fan ' + ('ON' if fan['on'] else 'OFF')
            if not fan['confirmed']: text += ' · Awaiting confirmation'
            elif fan['pending'] != 'NONE': text += ' · Checking temperature'
        self.fan_status.set(text)
        for mode, button in zip(('AUTO', 'ON', 'OFF'), self.fan_buttons):
            button.configure(style='Aqua.Primary.TButton' if fan['mode'] == mode else 'Aqua.TButton')
            button.state(['!disabled'] if fan['available'] and not self.busy else ['disabled'])
        for key, (status, button) in self.sensor_rows.items():
            item = controls['sensors'][key]
            reading = item['reading']
            if key == 'temp': value = temperature_units.format_reading(reading, self.temperature_unit)
            elif type(reading) is bool: value = 'Water detected' if reading else 'No water detected'
            else: value = str(reading if reading is not None else '')
            status.set(item['status'].title() + (' · ' + value if item['status'] == 'active' else ''))
            button.configure(text='Disable' if item['enabled'] else 'Enable')
        if self.sensor_rows:
            self.sensor_sync.set('Synchronized' if controls['inputApplied'] and controls['coolerApplied'] else 'Syncing sensor settings…')
        job = state['active'] or state['last']
        text = 'Schedule paused' if state['paused'] else 'Schedule enabled'
        if not state['ready']: text += ' · Feeder disconnected'
        if job: text += f"\n{job['cycles']} cycles · {job['status']}"
        self.feed_status.set(text)
        ready = state['ready'] and not state['busy'] and not pumps['pumpActive'] and not self.busy
        self.feed_button.state(['!disabled'] if ready else ['disabled'])
        running = 'Both pumps running' if pumps['extractionOn'] and pumps['insertionOn'] else 'Draining' if pumps['extractionOn'] else 'Refilling' if pumps['insertionOn'] else 'Pumps stopped'
        text = ('Manual' if pumps['manualOverride'] else 'Automatic') + ' · ' + running
        if not pumps['outputConnected']: text += ' · Disconnected'
        if pumps.get('pendingQuality'): text += ' · Checking water quality'
        self.pump_status.set(text)

    def refresh(self, schedule=True):
        try:
            success, text, clear_pin = self.results.get_nowait()
            self.busy = False
            for button in self.command_buttons: button.state(['!disabled'])
            self.message.set(text)
            if clear_pin:
                for var in self.pin_vars: var.set('')
            if self._last_status: self._render_status(self._last_status)
            self.worker.request()
        except queue.Empty:
            pass
        try:
            snapshot = self.worker.results.get_nowait()
            if snapshot != self._last_status:
                self._last_status = snapshot
                self._render_status(snapshot)
        except queue.Empty:
            pass
        if schedule: self.after_id = self.after(100, self.refresh)

    def close(self):
        self.worker.close()
        if self.after_id: self.after_cancel(self.after_id)
        self.destroy()
