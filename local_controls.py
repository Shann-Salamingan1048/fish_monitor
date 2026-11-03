"""Desktop controls use the same Python services as the browser dashboard."""
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser
import config
import temperature_units
import feeding_control
import water_control
import sensor_control
import fan_control
import admin_credentials
import threading
import queue

LIMIT_FIELDS = (
    ('tempLimit', 'Temperature warning (°C)', ('temperature', 'limit')),
    ('tempCriticalLimit', 'Temperature critical (°C)', ('temperature', 'overheatLimit')),
    ('phLowLimit', 'Lower', ('ph', 'lowLimit')),
    ('phHighLimit', 'Upper', ('ph', 'highLimit')),
    ('phCriticalLowLimit', 'Lower', ('ph', 'criticalLowLimit')),
    ('phCriticalHighLimit', 'Upper', ('ph', 'criticalHighLimit')),
    ('predictionDelaySeconds', 'Readings to watch before predicting (seconds)', ('predictionDelaySeconds',)),
    ('predictionAlertDurationSeconds', 'Keep prediction visible (seconds)', ('predictionAlertDurationSeconds',)),
    ('maxWebsiteViewers', 'Maximum dashboard viewers', ('maxWebsiteViewers',)),
    ('maxCameraViewers', 'Maximum camera viewers', ('maxCameraViewers',)),
    ('maxAdminSessions', 'Maximum signed-in Admin devices', ('maxAdminSessions',)),
)


class LocalControls(tk.Toplevel):
    def __init__(self, parent, schedule_callback, on_temperature_unit_change=None):
        super().__init__(parent)
        self.temperature_unit = "F" if config.get_db_value("gui_temperature_unit", "C") == "F" else "C"
        self.limit_labels = {}
        self.on_temperature_unit_change = on_temperature_unit_change
        self.title('AquaPi • Controls & settings')
        self.geometry(f'{min(780, self.winfo_screenwidth()-40)}x{min(560, self.winfo_screenheight()-60)}')
        self.minsize(620, 400)
        self.transient(parent)
        self.message = tk.StringVar(value='Local operator controls. Changes also appear in the web dashboard.')
        ttk.Label(self, text='Controls & settings', font=('Arial', 18, 'bold')).pack(anchor='w', padx=20, pady=(16, 6))
        ttk.Label(self, textvariable=self.message, wraplength=710).pack(anchor='w', padx=20, pady=(0, 12))
        tabs = ttk.Notebook(self)
        tabs.pack(fill='both', expand=True, padx=16, pady=(0, 12))
        self.tabs = {}
        for title in ('Feeding', 'Water pumps', 'Fan', 'Sensors', 'Camera', 'Display', 'Alert settings', 'Admin PIN'):
            page = ttk.Frame(tabs)
            tabs.add(page, text=title)
            canvas = tk.Canvas(page, highlightthickness=0)
            scroll = ttk.Scrollbar(page, orient='vertical', command=canvas.yview)
            canvas.configure(yscrollcommand=scroll.set)
            scroll.pack(side='right', fill='y')
            canvas.pack(side='left', fill='both', expand=True)
            frame = ttk.Frame(canvas, padding=16)
            window_id = canvas.create_window((0, 0), window=frame, anchor='nw')
            frame.bind('<Configure>', lambda event, c=canvas: c.configure(scrollregion=c.bbox('all')))
            canvas.bind('<Configure>', lambda event, c=canvas, i=window_id: c.itemconfigure(i, width=event.width))
            self.tabs[title] = frame
        display = self.tabs['Display']
        ttk.Label(display, text='Temperature unit', font=('Arial', 13, 'bold')).pack(anchor='w')
        self.unit_choice = tk.StringVar(value='Fahrenheit (°F)' if self.temperature_unit == 'F' else 'Celsius (°C)')
        self.unit_select = ttk.Combobox(display, textvariable=self.unit_choice,
            values=('Celsius (°C)', 'Fahrenheit (°F)'), state='readonly', width=22)
        self.unit_select.pack(anchor='w', pady=12)
        self.unit_select.bind('<<ComboboxSelected>>', self.select_temperature_unit)
        ttk.Label(display, text='Choose how temperatures appear on this desktop. Your choice is saved. '
                  'Readings, alerts and temperature limits update together.', wraplength=540).pack(anchor='w')
        feeding = self.tabs['Feeding']
        ttk.Label(feeding, text='Choose how much to feed now', font=('Arial', 13, 'bold')).pack(anchor='w')
        ttk.Label(feeding, text='1 cycle = open for 1 second, then close for 1 second.\nCycles are movements, not grams; calibrate the food amount first.').pack(anchor='w', pady=10)
        self.cycles = tk.StringVar(value='1')
        row = ttk.Frame(feeding); row.pack(anchor='w', pady=5)
        ttk.Label(row, text='Cycles (whole number):').pack(side='left', padx=(0, 10))
        ttk.Entry(row, textvariable=self.cycles, width=12).pack(side='left')
        self.feed_button = ttk.Button(row, text='Feed now', command=lambda: self.run(lambda: feeding_control.request_feed(self.cycles.get())))
        self.feed_button.pack(side='left', padx=10)
        ttk.Button(row, text='Stop feeder', command=lambda: self.run(feeding_control.stop_feeder)).pack(side='left')
        self.feed_status = tk.StringVar()
        ttk.Label(feeding, textvariable=self.feed_status, wraplength=680).pack(anchor='w', pady=14)
        row = ttk.Frame(feeding); row.pack(anchor='w')
        ttk.Button(row, text='Pause automatic feeding', command=lambda: self.run(lambda: feeding_control.set_paused(True))).pack(side='left', padx=(0, 8))
        ttk.Button(row, text='Resume automatic feeding', command=lambda: self.run(lambda: feeding_control.set_paused(False))).pack(side='left')
        ttk.Button(feeding, text='Edit feeding schedule', command=schedule_callback).pack(anchor='w', pady=14)
        ttk.Label(feeding, text='Pause skips scheduled feeds. Feed now does not change your schedule.\nStop cancels the active serving; it does not pause future schedules.').pack(anchor='w')
        pumps = self.tabs['Water pumps']
        self.water_schedule_window = None
        ttk.Button(pumps, text='Water-change schedule', command=self.open_water_schedule).pack(anchor='w', pady=(0, 12))
        ttk.Label(pumps, text='Manual control overrides automatic water changes.', font=('Arial', 12, 'bold')).pack(anchor='w')
        ttk.Label(pumps, text='Drain = remove tank water. Refill = add fresh water.\nStop draining / Stop refilling stops that pump only; the other keeps its state.\nManual pumps keep running until stopped. Stay with the tank.\nStop keeps automatic control paused; Resume auto restores it.').pack(anchor='w', pady=12)
        self.pump_status = tk.StringVar()
        ttk.Label(pumps, textvariable=self.pump_status, wraplength=680).pack(anchor='w', pady=12)
        for label, action in [('Start draining water', 'extract_start'), ('Stop draining', 'extract_stop'), ('Start refilling water', 'insert_start'), ('Stop refilling', 'insert_stop'), ('Run both pumps — drain + refill', 'both_start'), ('Stop pumps / pause auto', 'stop_all'), ('Resume automatic water control', 'auto_resume')]:
            ttk.Button(pumps, text=label, command=lambda a=action: self.run(lambda: water_control.manual_action(a))).pack(anchor='w', pady=5)
        fan = self.tabs['Fan']
        ttk.Label(fan, text='Cooling fan', font=('Arial', 13, 'bold')).pack(anchor='w')
        self.fan_status = tk.StringVar()
        ttk.Label(fan, textvariable=self.fan_status, wraplength=540).pack(anchor='w', pady=12)
        self.fan_buttons = []
        for label, mode in [('Auto — follow temperature', 'AUTO'), ('Fan on', 'ON'), ('Fan off', 'OFF')]:
            button = ttk.Button(fan, text=label, command=lambda m=mode: self.run(lambda: fan_control.set_mode(m)))
            button.pack(anchor='w', pady=5)
            self.fan_buttons.append(button)
        self.fan_explanation = ttk.Label(fan, text='Auto starts only at the critical temperature after 5 seconds and uses a 0.5°C cooling margin. '
                  'On and Off override temperature control, including when the temperature sensor is disabled. '
                  'Choose Auto to resume automatic cooling. Restarting the app restores Auto.',
                  wraplength=540)
        self.fan_explanation.pack(anchor='w', pady=12)
        ttk.Label(pumps, text='Only critical pH and Very Cloudy water start correction, after 5 seconds. Warning readings do not start pumps. '
                  'Normal or invalid readings reset the confirmation. Automatic correction runs both pumps; '
                  'Overflow and Very Low start immediate level correction; Low alone is a warning. Active refill continues until Normal.',
                  wraplength=540).pack(anchor='w', pady=12)
        sensors = self.tabs['Sensors']
        ttk.Label(sensors, text='Sensor activation', font=('Arial', 13, 'bold')).pack(anchor='w')
        ttk.Label(sensors, text='Disable a faulty sensor to exclude its readings, alerts and automatic actions. '
                  'Its associated protection is bypassed. Changes are saved and shared with the web dashboard.',
                  wraplength=540).pack(anchor='w', pady=(6, 12))
        self.sensor_rows = {}
        for key, (label, effect) in sensor_control.SENSORS.items():
            row = ttk.Frame(sensors)
            row.pack(fill='x', pady=7)
            status = tk.StringVar()
            ttk.Label(row, text=label, font=('Arial', 11, 'bold')).grid(row=0, column=0, sticky='w')
            ttk.Label(row, textvariable=status).grid(row=1, column=0, sticky='w')
            button = ttk.Button(row, command=lambda k=key: self.run(
                lambda: sensor_control.apply({k: not sensor_control.enabled(k)})))
            button.grid(row=0, column=1, rowspan=2, sticky='e', padx=(12, 0))
            ttk.Label(row, text=effect, wraplength=510).grid(row=2, column=0, columnspan=2, sticky='w')
            row.columnconfigure(0, weight=1)
            self.sensor_rows[key] = (status, button)
        self.sensor_sync = tk.StringVar()
        ttk.Label(sensors, textvariable=self.sensor_sync, wraplength=540).pack(anchor='w', pady=12)
        camera = self.tabs['Camera']
        ttk.Label(camera, text='Move the camera', font=('Arial', 13, 'bold')).pack(anchor='w')
        arrows = ttk.Frame(camera); arrows.pack(anchor='w', pady=15)
        for name, r, c in [('up',0,1), ('left',1,0), ('center',1,1), ('right',1,2), ('down',2,1)]:
            ttk.Button(arrows, text=name.title(), command=lambda cmd=name: self.run(lambda: self.camera_command(cmd))).grid(row=r, column=c, padx=4, pady=4)
        ttk.Button(camera, text='Open local dashboard / camera view', command=lambda: webbrowser.open(f'http://localhost:{config.WEB_PORT}/camera')).pack(anchor='w', pady=10)
        limits = self.tabs['Alert settings']
        self.limit_vars = {}
        values = config.get_public_limits()
        ph_groups = {}
        for row, title in ((1, 'pH · Warning range'), (2, 'pH · Critical limits')):
            group = ttk.LabelFrame(limits, text=title, padding=8)
            group.grid(row=row, column=0, columnspan=2, sticky='ew', padx=6, pady=6)
            group.columnconfigure((0, 1), weight=1)
            ph_groups[row] = group
        for i, (key, label, path) in enumerate(LIMIT_FIELDS):
            value = values
            for part in path:
                value = value[part]
            if key in ('tempLimit', 'tempCriticalLimit'):
                value = temperature_units.to_display(value, self.temperature_unit)
                label = label.replace('°C', '°' + self.temperature_unit)
            var = tk.StringVar(value=str(value)); self.limit_vars[key] = var
            r, c = divmod(i, 2)
            field = ttk.Frame(ph_groups[r] if r in ph_groups else limits)
            field.grid(row=0 if r in ph_groups else r, column=c, sticky='ew', padx=6, pady=4)
            self.limit_labels[key] = ttk.Label(field, text=label, wraplength=290)
            self.limit_labels[key].pack(anchor='w')
            ttk.Entry(field, textvariable=var, width=24).pack(fill='x')
            hint = {
                'predictionDelaySeconds': 'Example: 30 checks roughly the last 30 seconds for a rising or falling trend. It does not delay critical protection.',
                'predictionAlertDurationSeconds': 'How long a prediction stays visible once shown.',
                'maxAdminSessions': 'Default: 2. Lowering this signs out the least recently active Admin sessions.',
            }.get(key)
            if hint:
                ttk.Label(field, text=hint, wraplength=290).pack(anchor='w', pady=(4, 0))
        limits.columnconfigure((0,1), weight=1)
        ttk.Button(limits, text='Save alert settings', command=lambda: self.run(self.save_limits)).grid(row=7, column=0, sticky='w', pady=12)
        ttk.Label(limits, text='Outside the warning range: Warning. At or beyond a critical limit: Critical.',
                  wraplength=550).grid(row=6, column=0, columnspan=2, sticky='w', padx=6, pady=8)
        pin_panel = self.tabs['Admin PIN']
        ttk.Label(pin_panel, text='Change web Admin PIN', font=('Arial', 13, 'bold')).pack(anchor='w')
        self.pin_vars = [tk.StringVar() for _ in range(3)]
        for label, var in zip(('Current PIN', 'New PIN', 'Confirm new PIN'), self.pin_vars):
            ttk.Label(pin_panel, text=label).pack(anchor='w', pady=(12, 3))
            ttk.Entry(pin_panel, textvariable=var, show='•', width=30).pack(anchor='w')
        self.pin_save_button = ttk.Button(pin_panel, text='Save PIN', command=self.change_admin_pin)
        self.pin_save_button.pack(anchor='w', pady=14)
        self.pin_feedback = tk.StringVar(value='Changing the PIN signs out all web Admin sessions.')
        ttk.Label(pin_panel, textvariable=self.pin_feedback, wraplength=540).pack(anchor='w')
        self._pin_results = queue.Queue(maxsize=1)
        self._pin_after = None
        self.after_id = None
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _: self.close())
        self.refresh()

    def camera_command(self, command):
        if not config.send_to_identified_output_serial_ports(config.build_camera_serial_message(command)):
            raise ValueError('MegaOutput disconnected; camera command not sent.')
        config.update_servo_position_for_command(command)

    def change_admin_pin(self):
        values = [var.get() for var in self.pin_vars]
        self.pin_save_button.configure(state='disabled')
        self.pin_feedback.set('Saving…')
        def save():
            try:
                admin_credentials.change(*values)
                self._pin_results.put((True, 'PIN changed. Sign in again on the web.'))
            except Exception as exc:
                self._pin_results.put((False, str(exc)))
        threading.Thread(target=save, daemon=True).start()
        self._pin_after = self.after(50, self._finish_pin_change)

    def _finish_pin_change(self):
        try:
            success, message = self._pin_results.get_nowait()
        except queue.Empty:
            self._pin_after = self.after(50, self._finish_pin_change)
            return
        self._pin_after = None
        self.pin_feedback.set(message)
        self.pin_save_button.configure(state='normal')
        if success:
            for var in self.pin_vars: var.set('')

    def select_temperature_unit(self, event=None):
        unit = 'F' if self.unit_choice.get().startswith('Fahrenheit') else 'C'
        if self.on_temperature_unit_change:
            self.on_temperature_unit_change(unit)
        else:
            config.set_db_value('gui_temperature_unit', unit)
            self.change_temperature_unit(unit)

    def change_temperature_unit(self, unit):
        previous = self.temperature_unit
        self.temperature_unit = unit
        self.unit_choice.set('Fahrenheit (°F)' if unit == 'F' else 'Celsius (°C)')
        for key in ('tempLimit', 'tempCriticalLimit'):
            try:
                value = temperature_units.to_celsius(self.limit_vars[key].get(), previous)
                self.limit_vars[key].set(f'{temperature_units.to_display(value, unit):g}')
            except ValueError:
                pass
            label = self.limit_labels[key]
            label.configure(text=label.cget('text').replace('°' + previous, '°' + unit))
        self.refresh(schedule=False)

    def save_limits(self):
        payload = {k: v.get() for k, v in self.limit_vars.items()}
        for key in ('tempLimit', 'tempCriticalLimit'):
            payload[key] = temperature_units.to_celsius(payload[key], self.temperature_unit)
        return config.update_limits_from_payload(payload)

    def run(self, action):
        try:
            action()
            self.message.set('Request processed. Check the current device status below.')
            self.refresh(schedule=False)
        except (ValueError, OSError) as exc:
            self.message.set(str(exc))
            messagebox.showerror('Cannot complete request', str(exc), parent=self)

    def open_water_schedule(self):
        if self.water_schedule_window and self.water_schedule_window.winfo_exists():
            self.water_schedule_window.lift()
        else:
            from water_schedule_gui import WaterScheduleWindow
            self.water_schedule_window = WaterScheduleWindow(self)

    def refresh(self, schedule=True):
        if not self.winfo_exists():
            return
        margin = '0.9°F' if self.temperature_unit == 'F' else '0.5°C'
        self.fan_explanation.configure(text='Auto starts only at the critical temperature after 5 seconds and uses a ' + margin +
            ' cooling margin. On and Off override temperature control, including when the temperature sensor is disabled. '
            'Choose Auto to resume automatic cooling. Restarting the app restores Auto.')
        fan = fan_control.get_status()
        text = 'MegaOutput unavailable — connect it with updated fan firmware.'
        if fan['available']:
            text = fan['mode'] + ' • Fan ' + ('ON' if fan['on'] else 'OFF')
            if not fan['confirmed']: text += ' • Waiting for confirmation'
            elif fan['pending'] != 'NONE': text += ' • Checking temperature for 5 seconds'
        self.fan_status.set(text)
        for button in self.fan_buttons:
            button.configure(state='normal' if fan['available'] else 'disabled')
        controls = sensor_control.get_status()
        for key, item in controls['sensors'].items():
            status, button = self.sensor_rows[key]
            reading = item['reading']
            value = temperature_units.format_reading(reading, self.temperature_unit) if key == 'temp' else ('Water detected' if reading else 'No water detected') if type(reading) is bool else str(reading or '')
            status.set(item['status'].title() + (' • ' + value if item['status'] == 'active' else ''))
            button.configure(text='Disable' if item['enabled'] else 'Enable')
        self.sensor_sync.set('Settings saved on this Raspberry Pi. ' +
                             ('MegaInput synchronized.' if controls['inputApplied'] else 'Waiting for updated MegaInput to synchronize.') +
                             ('' if controls['coolerApplied'] else ' Temperature control: waiting for updated MegaOutput.'))
        state = feeding_control.get_status()
        job = state['active'] or state['last']
        text = 'Automatic feeding: ' + ('PAUSED' if state['paused'] else 'enabled')
        text += ' • MegaOutput: ' + ('ready' if state['ready'] else 'unavailable / update firmware')
        if job:
            text += f"\nLast serving: {job['cycles']} cycles • {job['status']}"
        self.feed_status.set(text)
        pumps = water_control.get_status()
        self.feed_button.configure(state='normal' if state['ready'] and not state['busy'] and not pumps['pumpActive'] else 'disabled')
        self.pump_status.set(('MANUAL' if pumps['manualOverride'] else 'AUTOMATIC') + ' • ' + ('Both pumps running' if pumps['extractionOn'] and pumps['insertionOn'] else 'Draining' if pumps['extractionOn'] else 'Refilling' if pumps['insertionOn'] else 'Pumps stopped') + (' • Output disconnected' if not pumps['outputConnected'] else ''))
        if pumps.get('pendingQuality'):
            self.pump_status.set(self.pump_status.get() + '\nChecking: ' + ', '.join(
                item['sensor'] + ' ' + str(item['seconds']) + '/5 seconds' for item in pumps['pendingQuality']))
        if schedule:
            self.after_id = self.after(1000, self.refresh)

    def close(self):
        if self.water_schedule_window and self.water_schedule_window.winfo_exists():
            self.water_schedule_window.close()
        if self._pin_after:
            self.after_cancel(self._pin_after)
        if self.after_id:
            self.after_cancel(self.after_id)
        self.destroy()
