# Latest update: navigation, Admin PIN and notifications

Read `UPDATE_NAVIGATION_PIN_AND_NOTIFICATIONS.md` for installation and the current behavior. The web now has separate pages; the desktop has an Admin PIN editor; pH settings use Warning range and Critical limits with Lower/Upper fields; notification work runs in a background worker. Keep your existing database and firmware. No Nano upload is needed for this update.

Earlier release notes below describe their respective updates.

# Previous update: web layout and two Admin sessions

Read `UPDATE_LAYOUT_AND_TWO_ADMINS.md`. The web order is System Status, Feeding Schedule, Quick Actions, Access Mode, Feed Your Fish Now, Sensor Activation. Two browser sessions can sign in as Admin; the desktop now has individual Stop draining and Stop refilling controls. Restart main.py after replacing the files, including the new admin_sessions.py.

# AquaPi — installation and latest input sketch

Read `requirements.txt` for the complete Raspberry Pi OS / Zorin setup, Python dependencies, Arduino libraries and optional cloudflared installation. OS packages are installed with apt; commented commands are not executed by pip.

The included input sketch is an exact copy of the uploaded `Nano_INPUT_Millis(2).ino`: pH 4.01 = ADC 1006.67; pH 9.18 = ADC 839.33. Turbidity ADC remains in serial output only. Custom feeding cycles remain available on the web camera page. The main web and desktop titles are now AquaPi.

# Latest change: two-point pH calibration

The INPUT Nano now uses **pH 4.01 → ADC 1006.67** and **pH 9.18 → ADC 839.33**. Read **UPDATE_TWO_POINT_PH.md** for the equation, middle-buffer check and upload instructions. If already on the previous critical-only/shutdown release, upload the INPUT Nano only.

# Latest change: critical-only automatic starts and GUI unit settings

Read **UPDATE_CRITICAL_ONLY.md** first. Warnings no longer start pump or fan correction. Critical readings start the existing recovery cycles; Very Low and Overflow are labeled Critical. The desktop unit choice is available under **Controls & settings → Display** and in the header.

If the previous shutdown/nonblocking release is installed, update the Pi files and upload the **OUTPUT Nano** only. Otherwise follow the matched Pi + both Nanos installation below. Preserve your database.

# Latest update: shutdown, nonblocking input and temperature units — 23 October 2026

Read **UPDATE_SHUTDOWN_AND_UNITS.md** first. This release stops actuators on app shutdown or output heartbeat loss, optimizes the input Nano, removes the connection notices, and adds Celsius/Fahrenheit preferences to the web and desktop interfaces.

**Update the Pi application and upload BOTH Nano sketches.** The input sketch includes the latest attached Pi calibration and −0.2°C correction. Keep your existing database. Older release instructions to upload only the output Nano do not apply to this release.

# Fish Monitoring — updated thesis system

This update keeps the two Arduino Nanos, Raspberry Pi, Flask dashboard, Tkinter monitor, sensor rules, camera and daily/weekly scheduler. The web and desktop controls share the same Python services and SQLite database.

## Sensor activation update — 14 October 2026

You can independently enable or disable **temperature, pH, turbidity, low float, normal float and upper float**.

- **Desktop:** Controls & settings → Sensors.
- **Localhost, LAN and remote web:** expand **Sensor activation** on the dashboard, graphs or camera page. Sign in as Admin to change settings; viewers can see which sensors are disabled.
- Changes use one shared backend, appear in the other interfaces on their next refresh and persist in the existing SQLite database. All sensors default to enabled on first installation.
- Disabled sensors show **DISABLED**. Their readings, predictions, alerts and sensor-specific automatic actions/protections are excluded. Disabling does not create a missing-sensor fault. Other enabled sensors, feeding schedules, manual controls and camera operation remain available.
- Disabling a sensor cancels an automatic pump action that depends on it. Disabling a float cancels the current automatic pump action so the next fresh packet can reassess the remaining floats. This is an operator cancellation, not a fault mode. Unrelated automatic actions continue; manual pump operation still requires manual Stop.
- Re-enabling waits for new readings; old queued packets and old prediction history cannot immediately trigger actions.
- The interface distinguishes settings saved on the Pi from Nano synchronization. Disconnected or outdated firmware shows synchronization pending. Software exclusions apply immediately, but hardware commands cannot take effect while the board is disconnected.

| Disabled sensor | Effect |
|---|---|
| Temperature | No temperature alerts or automatic cooling. In Auto, the fan turns off and the disabled sensor does not trigger a timeout. Explicit manual Fan On remains available. |
| pH | No pH alerts or pH-triggered replacement cycle. |
| Turbidity | No cloudiness alerts or turbidity-triggered water replacement. |
| Low float | No low/very-low trigger, refill request or low-level extraction protection from that switch. |
| Normal float | No normal-level stop. An enabled upper switch can stop refilling; overflow extraction stops when the enabled upper switch clears. |
| Upper float | No overflow trigger or upper-level stop from that switch. |

A disabled float is **not** interpreted as a dry switch. If the remaining switches cannot establish a level, the level shows **PARTIAL**. For example, a wet low switch with the normal switch disabled does not prove that the tank needs refilling. With all floats disabled, water level shows DISABLED; pH/turbidity may still start and stop quality correction, but there is no automatic level protection or refill based on those disabled floats. If the applicable stopping switches are disabled, stop a running pump manually; those protections are intentionally bypassed.

**Current installation:** follow UPDATE_SHUTDOWN_AND_UNITS.md and upload both Nano sketches with the updated Pi application.

## Professor's recommendations

| Recommendation | Implemented behavior |
|---|---|
| Custom feeding cycles / override | Enter a positive whole-number cycle count for each scheduled serving or a manual serving. Feed now, Stop feeder, Pause automatic feeding and Resume automatic feeding are available locally and on the web. |
| Fast localhost dashboard | Open **http://localhost:5000** on the Raspberry Pi. Pages, styles, scripts and graphs load locally without CDNs or internet access. |
| Beginner-friendly interface | System Status first, followed by Feeding Schedule and Quick Actions. Access Mode, Feed Your Fish Now and Sensor Activation are labeled expandable buttons at the bottom. Pump and alert controls retain readable labels. |
| Local custom / override window | **Controls & settings** opens desktop tabs for Feeding, Water pumps, Camera and Alert settings. **Schedule** edits daily/weekly times and cycle counts. |
| Optimizations | Offline Canvas graphs, nonoverlapping 2-second visible-page polling, bounded desktop update queue, serial reconnect, serial write timeout, additive database migration, atomic setting validation, and confirmed feeder completion. |

## Before replacing the old application

1. Stop your old running program. Do not run two copies against the same Nanos.
2. Back up the existing project and `fish_tank_state.db` while the program is stopped.
3. Extract this entire project. Keep `templates/` and `static/` in the shown folders; do not flatten the ZIP.
4. Copy your existing `fish_tank_state.db` next to the new `main.py`, or set `FISHTANK_DB_FILE` to its absolute path. Existing schedules gain a `cycles` column with **3 cycles**, preserving the old scheduled serving size.
5. Upload both Nano sketches in this release. The revised input preserves the latest supplied Pi calibration; both boards use the new startup/heartbeat protocol.

The application does not contain your historical database. Without your database it starts with no schedules and the existing code's default alert limits. Database paths now default to the project folder instead of the terminal's current directory.

## Arduino setup

- Open `arduino/NANO_OUTPUT/NANO_OUTPUT.ino` in Arduino IDE and upload it to the **output Nano**.
- Retain/install the Adafruit PWM Servo Driver library and its dependencies.
- Upload `arduino/Nano_INPUT_Millis/Nano_INPUT_Millis.ino` to the **input Nano**. Install the OneWire and DallasTemperature libraries. It includes the two-point pH calibration described in UPDATE_TWO_POINT_PH.md, individual float reports and the sensor enable mask.
- Choose the actual Nano board, processor/bootloader and USB port used by your boards. Each sketch has its own folder so Arduino IDE does not combine them.
- Baud rate remains **115200**. Pin assignments, servo angles and electrical active levels remain as in your supplied code.
- Output firmware identifies its feeder protocol as `FEEDER:V3`. An old output sketch can still identify the board, but custom feeding remains disabled until the new firmware is installed.

## Raspberry Pi installation

Use your current working Python environment if its packages are already installed. For a separate environment on Raspberry Pi OS:

```bash
sudo apt update
sudo apt install python3-venv python3-tk python3-opencv python3-picamera2
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run these commands inside the extracted project folder. The system-site-packages option makes the OS camera packages available in the environment. A configured Raspberry Pi camera is optional; camera streaming requires OpenCV and Picamera2.

### Local desktop and browser together

```bash
python main.py --open-browser
```

Or run `./start_local.sh`. Without the browser launch, use `python main.py` and choose **Open web** in the desktop monitor. Escape leaves full-screen mode; close the desktop window to stop the application and send shutdown commands.

### Browser only, without a desktop display

```bash
python main.py --headless
```

Open **http://localhost:5000** in a browser on the **same Raspberry Pi**. Keep the Python process running. Do not double-click `index.html`; Flask renders the templates and provides the live controls.

### Access from a phone or another computer on the same LAN

Network access is enabled automatically. Connect the phone to the same Wi-Fi as the Pi and run the application normally:

```bash
python main.py
```

The startup terminal prints `Phone / same Wi-Fi: http://<PI-LAN-IP>:5000` using the Pi's current IPv4 addresses. Open that address on the phone. On the Pi itself, `http://localhost:5000` still works. There is no need to run an export command or manually run `hostname -I`. `start_local.sh` and `--headless` use the same default.

The server defaults to `0.0.0.0`, listening on all IPv4 network interfaces. You can optionally set `FISHTANK_HOST=127.0.0.1` if you deliberately want access restricted to the Pi. A network with guest/client isolation may prevent devices from connecting to one another. This setting does not create an internet tunnel or configure the router.

The existing admin login remains in place. The default PIN remains **admin123** unless you configure `FISHTANK_ADMIN_PIN`; choose a private PIN for your network. Viewers can see readings but must sign in to change settings. The desktop remains a trusted local operator interface.

An existing Cloudflare tunnel can continue pointing at `http://localhost:5000`. Run one application process; the desktop, localhost and remote web interface share that process.

### USB port settings

Defaults remain `/dev/ttyUSB0` and `/dev/ttyUSB1`; messages identify each board's role. Prefer stable `/dev/serial/by-id/...` paths if available:

```bash
export FISHTANK_INPUT_PORT='/dev/serial/by-id/your-input-device'
export FISHTANK_OUTPUT_PORT='/dev/serial/by-id/your-output-device'
python main.py
```

Other supported environment variables include `FISHTANK_PORT` (5000), `FISHTANK_DB_FILE`, `FISHTANK_FEEDING_TIMEZONE` (Asia/Manila), and the existing viewer/feeding grace settings. The Pi clock must be correct. Run exactly one application process; do not launch multiple Flask workers for this hardware controller.

## Feeding guide

- **Updated cycle entry:** type values such as 30, 50 or 100 in either interface. Upload the new output sketch (V3), restart Python and refresh the browser. V2 firmware is deliberately refused because it still enforces the old cap.
- **Cycle:** feeder opens to the existing 180° position for 1 second, then closes to 0° for 1 second. Four cycles take about eight seconds.
- **Serving:** one manual command or one scheduled event containing your chosen positive whole-number cycle count. There is no arbitrary serving-size cap of 20 or 30. The Nano uses a 32-bit unsigned counter, so the technical maximum is 4,294,967,295 cycles; larger values are rejected instead of wrapping. Counts are not recommendations for fish ration sizes.
- **Frequency:** add multiple schedule rows at different times. Example: Monday 08:00 with 2 cycles and Monday 17:00 with 3 cycles means two servings on Monday. Repeat the desired times for other weekdays.
- **Feed now:** overrides the serving size for this request only. It does not alter or replace the next scheduled event.
- **Pause automatic feeding:** persists across restarts, cancels waiting scheduled servings and skips due occurrences while the worker is running. Manual feeding remains available.
- **Stop feeder:** closes the feeder and cancels the active serving. Future schedules remain enabled. Use Pause as well when you want no later automatic feeding.
- **Pump conflict:** manual feeding is rejected while pumping. Scheduled feeding waits. If a pump starts during a serving, the firmware cancels that serving; it is not replayed.
- **One serving at a time:** overlapping clicks are rejected. A serving's `complete` status comes from the Nano after all movements finish.
- **Unknown result:** a failed/partial write, lost connection or missing completion signal does not cause an automatic repeat. Inspect the feeder and press Stop before a new request. Hardware disconnection can prevent Stop from being delivered.
- **Backlog:** a claimed scheduled serving waits up to 30 minutes before expiring. A schedule that was never claimed uses the existing 5-minute catch-up window. This avoids many old feeds being delivered after a long outage.
- **Confirmation means movement sequence completed**, not proof that food actually fell; there is no food-weight or empty-hopper sensor in this design.

Calibrate how much food one cycle dispenses before selecting serving sizes. Select daily frequency and amount for your fish and feeder.

## Water and sensor behavior

Automatic water-quality/level rules use only enabled sensors. Drain removes water; Refill adds water. Manual pumps deliberately retain the existing behavior: they run until stopped and bypass automatic sensor stop conditions. **Stop pumps** leaves water automation paused. **Resume automatic control** restores it. Drain or Refill selects only that pump; Both pumps runs them together. Temperature and some other readings show `PAUSED` during pumping, according to the existing sensor policy.

The feeder runs the requested finite number of movements and has firmware pump interlocks. The original pump firmware does **not** have a communications-loss watchdog or maximum-run timer: if the Pi fails or USB is lost while a pump is running, a software stop cannot be guaranteed. Determine safe pump runtime/level cutoffs and test them with the real tank before unattended use. This update does not invent flow rates, tank volume, fish limits, or calibration values.

## Files and structure

```text
fish_monitor/
  main.py                    Desktop/web startup and shutdown
  config.py                  Shared configuration and SQLite migration
  feeding_control.py         Shared feeder state and Nano confirmations
  feeding_scheduler.py       Weekly schedules and postponement
  local_controls.py          New desktop operator window
  tkinter_gui.py             Local sensor display and schedule editor
  flask_server.py             Browser pages and JSON API
  mcu_comm.py                 USB serial communication and reconnect
  water_control.py            Automatic and manual pump decisions
  sensor_control.py           Shared sensor activation, persistence and Nano synchronization
  predictions.py             Original prediction logic
  templates/                 index.html, camera.html, graphs.html
  static/                    script.js, style.css
  arduino/                   Separate input and output Nano sketches
  tests/                     Python integration tests and C++ host simulation
```

## Verification and hardware acceptance

Automated software checks and known limitations are listed in `VALIDATION.md`. These tests simulate hardware; they are not a test on your Raspberry Pi, servo, camera or pumps.

Before your demonstration:

1. Upload the revised output Nano sketch, keep the working input firmware/calibration, connect both Nanos, and confirm live readings, synchronized sensor settings and a ready feeder.
2. With the feeder empty, request 1, 3 and 5 cycles from both interfaces. Count the actual movements and verify completion status.
3. Request several cycles, press Stop, and verify that the feeder closes immediately and stays stopped.
4. Add a schedule a few minutes ahead, confirm its selected cycle count, and check that it runs once. Pause it and verify it does not run.
5. Test pump interlocks with a supervised test setup; verify real pumps stop when commanded. Confirm the pump behavior described above.
6. Open the dashboard with internet disconnected. Check readings, graph drawing, schedule changes and the local camera.
7. Verify alert values saved in one interface appear in the other. Restart and confirm schedule persistence.
8. Calibrate food amounts, probe readings and operating limits for the actual tank.

### Check sensor overrides before use

With a supervised test setup, disable pH during a pH-triggered water replacement and confirm the pump stops. Confirm that disabled pH values no longer create alerts while temperature and turbidity still update. Test each float separately, verifying its disabled input does not create a false dry/overflow condition. Disable temperature while cooling is on and confirm the relay switches off. Re-enable sensors and confirm fresh readings return. Change a setting from the web and verify it on the desktop; restart and reconnect the boards to verify the saved choices are restored. Check these behaviors on the actual equipment before using the override in the aquarium.


## Dashboard layout update — 14 October 2026

The main dashboard now shows System Status alongside Quick Actions, then Weekly Feeding Schedule. Below these are three labeled buttons in order: Access Mode, Feed Your Fish, and Sensor Activation. Tap a button to expand its controls; tap again to close it. Opening another section closes the previous one. Sections start closed for viewers and administrators. Sign in through Access Mode to enable editing and feeding. Disabled-sensor notices remain visible even when Sensor Activation is closed.

The shared layout applies to local and Cloudflare access; the camera and graph pages also place their expandable controls below their main content. For this layout-only update from the automatic LAN/sensor-activation release, replace `static/script.js`, `static/style.css`, `templates/index.html`, `templates/graphs.html`, and `templates/camera.html`. Restart the application and reload your browser. Backend Python files, saved schedules, sensor settings and Nano firmware do not need replacement for this layout update.


## Input Nano pH calibration

The current input sketch uses two points: pH 4.01 / ADC 1006.67 and pH 9.18 / ADC 839.33. Startup shows `PHCAL:NANO_2POINT_V1`. Sensor packets include `pHADC` alongside the pH calculated from the same filtered sample. See UPDATE_TWO_POINT_PH.md for the current formula and validation procedure; older three-point constants and diagnostics are superseded.
