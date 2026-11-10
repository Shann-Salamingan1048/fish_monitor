# AquaPi — Mega 2560 edition

One Arduino Mega 2560 reads the sensors and controls the pumps, fan relay and PCA9685 servos. One USB connection links it to the Raspberry Pi, which hosts the GUI, website, camera video and feeding schedule.

- **Firmware:** `arduino/AquaPi_Mega/AquaPi_Mega.ino`. This is the entire firmware; there is no accompanying project header or second sketch.
- **Installation and wiring:** see `MEGA_SETUP.md`.
- **Dependencies:** see `requirements.txt` for Python, system packages and Arduino libraries.
- **Validation:** see `VALIDATION.md`.

The firmware and Python use the same serial names: `MegaInput`, `MegaInputStatus`, `MegaOutput`, `MegaServo`, `MegaConfig` and `AquaPiMega`. Input/output are logical roles on the same physical board, not separate devices. USB speed is 115200 baud.

Use the included combined Mega firmware when setting up a new controller. This dashboard and laptop-preview update requires only the application files if that sketch is already installed. Extract into a clean folder so obsolete sketch files are not included accidentally.

Automatic pump/fan decisions run locally on the Mega. Saved settings and calibration are synchronized from the Pi into controller RAM. System Off and the four-second heartbeat timeout stop the actuators. USB reconnection leaves pumps stopped until an operator resumes automatic control or chooses a manual action.

Keep your database and environment settings when upgrading. The default database is `fish_tank_state.db`, or the path specified by `FISHTANK_DB_FILE`. Preserve your chosen Developer option setting in `main.py`.

Run the application from the project folder:

```bash
python main.py
```

Headless operation:

```bash
python main.py --headless
```

The Pi can open `http://127.0.0.1:5000`. On a phone connected to the same Wi-Fi, open `http://<Pi-Wi-Fi-IPv4>:5000` while AquaPi is running. Use the Pi’s current address from its Wi-Fi details. Existing Cloudflare tunnel configuration continues to point to that service. Close Arduino Serial Monitor while AquaPi owns the USB port.

One Mega does not guarantee a cure for pump-induced USB disconnections. Electrical interference or supply/ground disturbances still need hardware diagnosis if they persist.

## Start automatically on Raspberry Pi OS

On the Pi desktop, open the copy of `install_autostart.py` beside your working `main.py` in Thonny. Click **Run** once and approve the Pi password prompt. Setup enables automatic desktop login and retires the previous boot service. After the success message, restart when the feeder and pumps are idle. AquaPi opens its desktop window and localhost dashboard. An existing Cloudflare Tunnel service is enabled for boot as well.

## Tank defaults

| Setting | Default |
|---|---|
| Three-point pH | 4.01 at ADC 917, 6.86 at 828.33, 9.18 at 758.11 |
| Turbidity | 0–399 Very Cloudy, 400–599 Cloudy, 600–1023 Clear |
| Feeding | Every day at 07:00 and 19:00, three cycles each, in the tank timezone (default Asia/Manila) |
| Temperature | Warning at 29 °C, critical at 32 °C |
| pH | Warning at or beyond 6.5 and 8.0, critical at or beyond 6.0 and 8.5 |

The Pi sends calibration and protection thresholds to the combined Mega firmware on connection. The packaged database starts in normal monitoring mode. Upgrades keep saved calibration profiles and nonempty feeding schedules. On the first upgrade, an empty schedule gets the two defaults. Edits and deletions then survive restarts. Temperature and pH limit groups matching the previous factory values upgrade once, independently. A customized group keeps its settings.

## Laptop preview

In `main.py`, uncomment the following line, save, then run `python main.py` from this folder. For a browser-only preview, run `python main.py --headless --open-browser`.

```python
config.SIMULATE_SENSORS = True
```

The desktop and web dashboard show sample temperature, pH, level, clarity and raw ADC readings. Sign in as Admin to see the live ADC-to-NTU calculation in Developer option. The preview uses its own `fish_tank_preview.db`. Sensor readings update every second. Hardware and scheduled jobs stay offline. To use the Mega, put the `#` back at the start of the line and restart.

Choose **System settings → Display → Theme** on the web, or use the **Light/Dark** control on the desktop toolbar. Each browser and the desktop remember their own choice.

## Turbidity Developer option

View live ADC alongside the 5 V curve estimate or an interpolation from saved NTU references. Choose ADC or NTU clarity ranges, add known-NTU reference points, and save without re-uploading the Mega. Existing ADC calibration remains compatible. See `TURBIDITY_NTU.md` for the steps and distinction between web NTU and the separate serial diagnostic.

## Scheduled water changes

Create weekly or everyday drain/refill schedules in Web → Water Changes → Water-change schedule, or use Water-change schedule beside Feeding Schedule on the GUI dashboard. The same editor opens from Controls & settings → Water pumps. Both use the same database. This feature requires the updated combined Mega sketch. See `WATER_CHANGE_SCHEDULE.md` for setup, durations, float protection and skipped-run behavior.

## Turbidity predictions

The Pi uses recent raw ADC readings to estimate when the trend may enter the next calibrated range. Both rising and falling ADC can produce a forecast. Notifications use the destination range’s exact display label, including when adjacent ranges share a category. A newly detected forecast replaces the previous one across sensors. If forecasts start in the same sample, the shortest ETA wins. Limit warnings and critical alerts stay visible independently. Predictions appear in desktop and web notifications after the configured Prediction history interval and sufficient consistent readings. Calibration, disabled sensors, missing readings and reconnects reset this observation period. ADC forecasts also follow ranges configured with NTU reference calibration.

## Aquarium overview

The web overview follows the tank timezone (Asia/Manila by default). Automatic scene lighting uses dawn from 05:00, day from 07:00, dusk from 17:00 and night from 19:00. Scene previews and Pause motion are local to the browser. The scene is illustrative and the Camera page shows the live tank.

Real-Time Readout shows water temperature, pH, level and clarity. Expand a reading to inspect its recent range or open its graph. The clarity readout, scale and graph use the display names of the active turbidity calibration ranges. The scene starts with 10 animated fish. Select Add fish, set the quantity, then tap a species to add that many. The minus button removes the last fish. Clear all fish empties the aquarium in this browser. Each browser remembers its school of 0–24 fish. Tap Sensors to show or hide the readings. Fish follow Pause motion and the device’s reduced-motion setting. The clarity graph shows the last 30 samples collected on the Graphs page. For raw ADC and estimated NTU, open Developer option and find Turbidity calibration.
