# AquaPi — Mega 2560 edition

One Arduino Mega 2560 reads the sensors and controls the pumps, fan relay and PCA9685 servos. One USB connection links it to the Raspberry Pi, which hosts the GUI, website, camera video and feeding schedule.

- **Firmware:** `arduino/AquaPi_Mega/AquaPi_Mega.ino`. This is the entire firmware; there is no accompanying project header or second sketch.
- **Installation and wiring:** see `MEGA_SETUP.md`.
- **Dependencies:** see `requirements.txt` for Python, system packages and Arduino libraries.
- **Validation:** see `VALIDATION.md`.

The firmware and Python use the same serial names: `MegaInput`, `MegaInputStatus`, `MegaOutput`, `MegaServo`, `MegaConfig` and `AquaPiMega`. Input/output are logical roles on the same physical board, not separate devices. USB speed is 115200 baud.

This release requires updating the Python application **and** uploading the included firmware together. Old protocol versions cannot be mixed with this one. Extract into a clean folder so obsolete sketch files are not included accidentally.

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

## Turbidity Developer option

View live ADC and calibrated NTU together. Choose ADC or NTU clarity ranges, add known-NTU reference points, and save without re-uploading the Mega. Existing ADC calibration remains compatible. See `TURBIDITY_NTU.md` for the steps and distinction between web NTU and the separate serial diagnostic.

## Scheduled water changes

Create weekly or everyday drain/refill schedules in Web → Water Changes → Water-change schedule, or GUI → Controls & settings → Water pumps → Water-change schedule. Both use the same database. This feature requires the updated combined Mega sketch. See `WATER_CHANGE_SCHEDULE.md` for setup, durations, float protection and skipped-run behavior.

## Turbidity predictions

The Pi uses recent raw ADC readings to estimate when the trend may enter the next calibrated range. Both rising and falling ADC can produce a forecast. Notifications use the destination range’s exact display label, including when adjacent ranges share a category. These predictions appear in desktop and web notifications after the configured Prediction history interval and sufficient consistent readings. Calibration, disabled sensors, missing readings and reconnects reset this observation period. ADC forecasts also follow ranges configured with NTU reference calibration.

## Aquarium overview

The web overview follows the tank timezone (Asia/Manila by default). Automatic scene lighting uses dawn from 05:00, day from 07:00, dusk from 17:00 and night from 19:00. Scene previews and Pause motion are local to the browser. The scene is illustrative and the Camera page shows the live tank.

Real-Time Readout shows water temperature, pH, level and clarity. Expand a reading to inspect its recent range or open its graph. The clarity readout, scale and graph use the display names of the active turbidity calibration ranges. The animated fish follow Pause motion and the device’s reduced-motion setting. The clarity graph shows the last 30 samples collected on the Graphs page. For raw ADC and estimated NTU, open Developer option and find Turbidity calibration.
