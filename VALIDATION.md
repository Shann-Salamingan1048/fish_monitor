# Simultaneous scheduled water changes

- 322 Python tests passed, including a 60-second shared timer, Low/Very Low/High correction, stable Normal recovery with float wobble, immediate opposite-limit correction, ordered pump acknowledgements, missing adjustment replies, no repeated commands, completion during correction, read-only countdown polling, legacy schedules, operator stop, invalid readings and feeder exclusion.
- The web schedule DOM test passed for create, edit, pause, delete, polling, remaining-time and correction-state display, and stop with one duration field. The desktop help text and status were updated; visual desktop verification remains pending.
- Eight web pages rendered. The updated JavaScript passed its syntax check.
- The existing Mega simulation passed for simultaneous pump commands, heartbeat expiry and system shutdown.
- A byte comparison confirmed that all bundled Arduino firmware files match the prior release.

Physical pump timing and flow, float response and Pi reboot behavior require validation on the installed aquarium.

## Earlier verification

# AquaPi control and calibration validation

## Boot, defaults and forecast update

The updated Python suite covers forecast replacement across sensors, retention of critical alerts, daily defaults after restarts and deletions, ADC cutoffs and saturation of the 5 V estimate. All six combined-Mega simulations and all four browser DOM suites passed. The generated service passed `systemd-analyze verify`. Two headless launches with the packaged database served the dashboard API, loaded the requested limits and schedules, and shut down on SIGTERM. Hardware boot, actual NTU accuracy, pump flow, and Pi frame times require the installed aquarium.

## Previous calibration update

- 268 Python tests passed. Coverage includes 2/3-point calibration persistence, input validation and Admin API access; temperature display/alerts during pumping; feeding postponement beyond 30 minutes and explicit not-started hardware conflicts; existing schedule, serial reconnect and security regressions.
- Six C++ simulations passed using the actual combined `.ino`: core firmware, automatic policy, scheduled water changes, turbidity and the control/calibration cases and all two-point buffer pairs.
- Pair-selection checks cover all three pairs, selected-only ADC serialization, saved profile reload, invalid pairs/spans, three-point continuity, exact buffer endpoints and midpoint interpolation, spike rejection and filter convergence. Web checks cover pair switching and hiding unused fields.
- New firmware checks cover 2/3-point formula switching and buffer endpoints, invalid configuration rejection, Low and Very Low refilling, 3-second Normal allowance, float bounce, opposite-level priority, timer rollover, simultaneous quality triggers, temperature sampling and cooler activation while both pumps run.
- Web jsdom checks passed for calibration method selection, acid-field visibility, draft retention, saving/reloading 3 points and returning to 2 points, existing NTU/ADC editing and scheduled water-change controls. Eight web pages rendered successfully.
- Actual Mega AVR compilation passed: **34,238 bytes flash (13%), 3,832 bytes static RAM (46%), 4,360 bytes remaining for stack/heap**. This is not a measured worst-case stack watermark.

GUI changes were limited to removing the requested notice and shortening text; a full Tk display check was unavailable. No physical pumps, probes or float switches were connected. Test the 3-second allowance against your real pump flow and float positions before leaving the system unattended.

## Repeat checks

```bash
python -m pytest -q
node --check static/water_schedule.js
python tests/render_pages.py
# Development-only Node dependency: npm install --no-save jsdom
node tests/frontend_water_schedule_test.cjs
arduino-cli compile --fqbn arduino:avr:mega:cpu=atmega2560 arduino/AquaPi_Mega
g++ -std=c++17 -I tests/firmware_stubs tests/mega_water_schedule_test.cpp -o /tmp/aquapi-water-test
/tmp/aquapi-water-test
```

Also run the existing `mega_firmware_test.cpp`, `mega_policy_test.cpp` and `mega_turbidity_test.cpp` and `mega_control_update_test.cpp` and `mega_ph_pairs_test.cpp` in the same way. Arduino toolchain: AVR Boards 1.8.8, OneWire 2.3.8, DallasTemperature 4.0.6, Adafruit PWM Servo Driver Library 3.0.3 and Adafruit BusIO 1.17.4.

