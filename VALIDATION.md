# Developer calibration and pH settling — 31 October 2026

- 186 Python tests passed, covering pH anchors/midpoint, elapsed-time smoothing, invalid/stale ADC handling, revision/reconnect checks, SQLite persistence and failed-save atomicity, Admin access, disabled-feature routes, cross-site writes, range boundaries/validation, arbitrary labels and their alert/pump behavior, calibration-mode interlocks, settling restart and fresh critical confirmation.
- Actual Flask + Chromium browser checks passed for nine pages at 390 px and 1366 px: no JavaScript page errors or horizontal overflow. Exercised Admin navigation, start/save/reload/finish, adding/removing rows, rejected overlaps, safe display of HTML-like labels, saved values and disabled form controls after finishing. Screenshots were visually inspected. No hardware was attached to the browser test server.
- Python/JavaScript syntax checks passed. Both Nano sketches are byte-identical to the prior version; no firmware changes or new runtime dependencies were required. Historical Nano memory/build measurements below still describe the same firmware.
- No physical aquarium/probe verification, live Tk display test, or Raspberry Pi performance measurement was possible. A 120-second delay is not a stability/accuracy guarantee.

Historical validation records follow.

# Current input firmware correction: manual constants, no EEPROM

Five host-simulation configurations passed: unset, synthetic measured pair, equal pair, reversed pair and ADC rail. Actual Nano builds passed: 12,152 bytes flash with unset constants; 12,398 with a synthetic configured pair; 703 bytes static RAM in both. The distributed constants remain explicit zero placeholders because the user will measure and enter them. No physical probe accuracy test was performed. The Python/UI/output firmware are unchanged from the prior release; earlier validation records below are historical. See UPDATE_MANUAL_PH_CONSTANTS.md for details.

# Prediction settings, Admin capacity and input calibration — 30 October 2026

156 Python tests passed; eight-page DOM checks and rendered browser checks passed at 390/1366 px. Input and output firmware host simulations passed. The input sketch compiled for the actual Arduino Nano ATmega328P Old Bootloader target: 14,230 bytes flash, 747 bytes static SRAM (1,301 bytes left for runtime). The enum declaration-order compile error in the uploaded sketch was corrected. See UPDATE_PH_CALIBRATION_AND_ADMIN_LIMIT.md for review details and library versions. No physical probe calibration verification or live Tk display test was performed.

# Navigation, PIN and notification update — 30 October 2026

- 150 Python tests passed, including hashed PIN persistence, both-session revocation, invalid-change rejection, all eight routes, background snapshot coalescing and unchanged notification redraw suppression.
- Eight-page DOM integration passed: role gating, navigation, schedules, custom camera/Feeding cycles, truthful completion status, water interlocks, fan/pumps, sensor toggles, units and settings save.
- Rendered Chromium checks passed at 390 px and 1366 px: no page exceptions or horizontal overflow; navigation and browser Back work. Representative screenshots were visually inspected. Browser replies were simulated.
- Python and JavaScript syntax passed. Both Nano sketches remain byte-identical to the previous archive. No firmware rebuild was needed.
- No physical hardware or live Tk display test was performed; Pi responsiveness remains to be checked on-device. Historical records below refer to earlier updates.

# Two-point input calibration verification

Actual Nano input build passed: 11,668 bytes flash and 694 bytes static SRAM. Input firmware simulation passed the two anchors, midpoint, intermediate values, removal of the third calibration diagnostic and paired pH/pHADC output, alongside the existing nonblocking acquisition and shutdown tests. Physical buffer verification is pending. Python and output firmware are unchanged from the critical-only release below.

# Critical-only update verification

112 Python tests passed. Output firmware simulation and actual Arduino Nano compilation passed (15,468 bytes flash; 929 bytes static SRAM). New tests verify warnings do not start correction, critical boundaries and configurable thresholds do, recovery cycles finish, and the GUI Display choice synchronizes with the header and persists. Python/JavaScript syntax passed. Input firmware is unchanged from the previous verified release. Physical/graphical hardware validation remains outstanding.

# Latest verification — 23 October 2026

- Actual Arduino Nano builds passed for both sketches (Arduino AVR 1.8.8). Input: 11,876 bytes flash, 694 bytes static SRAM; output: 15,468 bytes flash, 929 bytes static SRAM. See UPDATE_SHUTDOWN_AND_UNITS.md for library versions and the original-input comparison.
- 91 Python tests passed, including shutdown races, serial startup/heartbeat routing and desktop Fahrenheit-limit conversion.
- Both C++ firmware simulations passed. Input checks asynchronous acquisition, fixed-buffer transmission, commands during sampling, sensor masking, pH anchors, −0.2°C correction, disconnection handling, standby/timeout and rollover. Output checks pump/fan/feeder shutdown, blocked commands while off, heartbeat timeout, servo PWM disable, rollover and existing actuator behavior.
- DOM checks passed on dashboard, graphs and camera pages, including unit selection, persisted browser preference, Fahrenheit display/axes, Celsius threshold payloads and removal of the connection badge.
- Python syntax and JavaScript syntax checks passed.
- Physical aquarium hardware and a desktop graphical display were unavailable. The historical “no AVR toolchain build” note below applies only to the earlier release; the current sketches were compiled with the actual AVR toolchain.

## Earlier release verification

# Daily schedules and both pumps — 21 October 2026

- **78 Python tests passed** against isolated SQLite databases and simulated serial devices. New coverage includes daily feeding on every weekday and weekend, once-per-date claims, next-day display, midnight grace, pause/edit/delete, old CHECK-constraint migration preserving IDs/history/cycles and the autoincrement sequence, both-pump commands, immediate level-priority transitions, quality recovery, manual overrides, reconnect state, disabled sensors and feeder conflicts. Prior fan, feeding and sensor checks also pass.
- **Output Nano C++ host simulation passed** using the actual revised sketch, including BOTH_ON, both-to-extract and both-to-insert transitions, STOP_ALL, cancellation/rejection of feeding during pumping, and prior fan timing checks.
- **Frontend DOM integration passed** on dashboard, graphs and camera. Verified removed turbidity fields, the both-pump request/display, daily schedule label/save/edit, and existing fan/sensor permissions and controls. JavaScript syntax and Python compilation passed.
- The actual desktop schedule date formatter and option mapping were exercised without launching a graphical window. No rendered phone/browser or Tk window layout was inspected in this run.
- The input Nano sketch is byte-identical to the preceding fan release. It does not contain the newer Pi-connected calibration values, which were not supplied. Do not upload it for this update.

These are software checks, not an AVR target build or physical hardware validation. Verify pump switching on the actual system using the installation notes in UPDATE_DAILY_AND_PUMPS.md. No hardware tests were performed here.

# Fan and confirmation update — 16 October 2026

- **62 Python tests passed.** Added deterministic-clock checks for brief/sustained pH and turbidity conditions, recovery resets, missing/invalid packets, opposite pH conditions, sensor toggles and reconnection, threshold/manual resets, immediate float protection, fan admin authorization, strict request validation, output capability gating, acknowledgements, stale/disconnected status, reconnection sync, write failures and shutdown.
- **Output Nano C++ host simulation passed.** Covers manual On/Off/Auto, explicit temperature disable, five-second hot/cool confirmation, hysteresis, a lone spike followed by silence, interrupted sampling, invalid temperature, periodic fan status and the existing feeder/pump interlocks and cycle tests.
- **Browser DOM checks passed** on dashboard, graphs and camera pages: admin/viewer fan permissions, command payload, pending/confirmed status, selected mode, quality-check message and all existing sensor-control checks.
- Python compilation and JavaScript syntax passed. Flask templates rendered successfully for the DOM checks.
- The input Nano sketch was not modified. The last shared pH constants in the archive may differ from the user's later successful Pi-connected calibration; installation explicitly preserves the working input firmware.

No physical relay/fan/pump test, AVR target build, rendered browser screenshot or live Tk desktop test was performed. Hardware acceptance remains necessary. Prior records below describe earlier releases.

# Calibration-formula integration — 16 October 2026

Copied the standalone calibration sketch's array/endpoint formula and trimmed sampling into the full input firmware, explicitly selected DEFAULT reference and added a startup calibration identifier plus paired pHADC diagnostics. The previous formula was mathematically equivalent; no physical cause of the reported discrepancy is established.

The actual input sketch passed C++ host simulation: all three anchors, both segment midpoints, acquisition at ADC 791 producing pH 9.17 in the serial packet, DEFAULT reference selection, calibration startup identifier, disabled/paused diagnostic masking, configuration reset, float behavior and existing sensor controls. Inspected the current Pi parser: it reads named fields and ignores the additional pHADC field. No physical hardware test or AVR target build was performed. Full backend tests were not repeated because backend code did not change.

# Input Nano pH recalibration — 16 October 2026

Updated the three ADC anchors to the user's new Nano measurements: 949.56 at pH 4.01, 865.56 at pH 6.86, and 790.78 at pH 9.18. The actual input sketch passed its C++ host simulation, including all three conversion anchors, sensor disabling, float handling and configuration revisions. No physical buffer retest or AVR target build was performed here. Matching calibration anchors is not independent accuracy validation. Only input-Nano calibration constants, corresponding test data and documentation changed from the dashboard layout release.

For this calibration update, upload only arduino/Nano_INPUT_Millis/Nano_INPUT_Millis.ino to the input Nano. Keep the same power and wiring and recheck all three buffers after uploading. No Python or output-Nano update is needed.

# Dashboard layout update — 14 October 2026

JavaScript syntax and DOM integration checks passed on the dashboard, graphs and camera pages. Verified main-content order, the three labeled controls at the bottom, collapsed initial state, expanding/closing sections, single-panel expansion, unique IDs, admin/viewer permissions, login focus, unchanged 30-cycle feed request, sensor enable/disable, disabled notices, re-enable state, and preserved panel state during role refresh. Existing sensor DOM checks passed within this run.

Only the shared frontend assets and three HTML templates changed, plus documentation. Python backend and Nano files match the previous archive. Browser installation failed because the download was invalid, so rendered desktop/phone appearance could not be verified here. No physical hardware test was performed for this layout update.

# Automatic LAN access update — 14 October 2026

Default binding changed from 127.0.0.1 to 0.0.0.0. Normal startup now prints the Pi's IPv4 phone links. Verified the default configuration, network socket binding, IPv4 selection from the supplied hostname output, fallback when hostname is unavailable, and Python syntax. Full application startup was not repeated for this small update because runtime packages were unavailable in the current verification environment. No phone-to-Pi test was performed here. Nano firmware is unchanged from the sensor activation release below.

# Sensor activation update verification — 14 October 2026

- **47 Python tests passed**, including the existing feeding/schedule checks and new sensor integration tests. Covered persistence, admin-only updates, malformed requests, cross-interface shared state, concurrent sensor packets and toggles, cancellation of sensor-owned pump actions, partial float configurations, all sensors disabled, fresh data after re-enable, prediction suppression, and continued feeding.
- **Both actual Nano sketches passed C++ host simulations.** Input checks cover skipped acquisition, individual float readings, configuration revisions, idempotent synchronization and the supplied pH calibration points. Output checks cover explicit temperature disable, cooler off, ignored disabled temperature packets, no disabled-temperature timeout, restoration of the timeout after re-enable, and existing feeder interlocks.
- **Frontend DOM checks passed** for the dashboard, graphs and camera pages: six controls, admin/viewer access, request payload, disabled display, re-enable waiting state, all-disabled summary and server restart. Existing feed and schedule DOM checks also passed.
- Python syntax and JavaScript syntax checks passed.

These are software and host-simulation checks, not physical Raspberry Pi/Nano tests. No AVR target build was available. The browser download failed in this environment, so frontend verification used jsdom rather than rendered screenshots; a Tk desktop display was unavailable. Verify layout and actual switching on the Pi and phone. Both revised Nano sketches must be compiled/uploaded with the correct board and libraries before hardware use.

The tests folder is optional on the deployed Pi; it is included for repeatable verification. Runtime code does not import it.

## Earlier verification record


Validation performed on the updated project, 10 October 2026.

| Check | Result |
|---|---|
| Python integration suite | **30 passed** using isolated temporary SQLite databases and simulated serial devices. |
| Python syntax compilation | Passed for all project Python modules. |
| JavaScript syntax | Passed with `node --check static/script.js`. |
| Output Nano host simulation | Passed C++ compilation and assertions for exact cycle completion, out-of-range/fractional rejection, duplicate overlap rejection, stop/close, and feeder/pump interlocks. |
| Frontend DOM checks | Passed script initialization, admin/viewer visibility, custom feed payload, busy state, schedule cycle rendering/editing, and offline chart initialization with a Canvas stub. |
| Web routes and access | Flask test client verified all three pages, both local assets, admin protection, schedule create/update, custom feed dispatch, and the legacy feed route using the shared guard. |
| Existing data migration | Verified old schedule table migration, preserved schedule identity, default 3 cycles, and repeat initialization. |

The suite also checks simultaneous requests, pause/resume behavior, cancellation of paused pending schedules, pump postponement, 30-minute expiry, uncertain-write handling without replay, completion feedback, stop acknowledgement, and atomic limit validation.

## Remaining verification

- **No physical hardware test**: the Raspberry Pi, input Nano, output Nano, servo, camera, probes and pumps were not available in this environment.
- **No AVR toolchain build**: the output sketch was compiled and exercised with host stubs. Select the actual Nano target and compile/upload it in Arduino IDE before the demonstration.
- **No rendered visual QA**: launching Chromium was blocked by the execution environment's socket restrictions. Tkinter could be imported, but no graphical display was available. DOM checks are not a substitute for checking text fit and layout on your Pi display and phone.
- Serial completion proves that firmware finished the movement sequence; it does not measure food delivery or verify physical servo position.
- Pump and sensor calibration, safe feed quantity, and aquarium limits remain specific to your actual equipment and fish. See the hardware acceptance steps in README.md.

## Re-run software checks

From the project folder, with Flask, pyserial, OpenCV and pytest available:

```bash
python -m compileall -q .
python -m pytest -q tests
node --check static/script.js
```

If a desktop C++ compiler is installed:

```bash
g++ -std=c++17 -I tests/firmware_stubs tests/firmware_test.cpp -o /tmp/fish_firmware_test
/tmp/fish_firmware_test
```

The DOM harness used a temporary jsdom installation and mocked server replies/Canvas drawing; it is an additional development check, not part of the Raspberry Pi runtime.


Cycle-cap removal verification: accepts 21, 30, 100, 65,536 and 4,294,967,295 through the Python scheduled-feed path. Firmware host simulation executes all 30 cycles, checks counts above the old 16-bit argument size, and verifies cancellation at the uint32_t boundary. Zero, negative, fractional and counter-overflow values remain invalid. Earlier capped V2 firmware is rejected by the updated application. These are simulated checks, not physical hardware tests.

Input sketch host simulation:

```bash
g++ -std=c++17 -I tests/firmware_stubs tests/input_firmware_test.cpp -o /tmp/fish_input_test
/tmp/fish_input_test
```

Frontend checks (`jsdom` is a development dependency, not a Pi requirement): run `python tests/render_pages.py`, then `node tests/frontend_sensor_test.cjs` with jsdom available on `NODE_PATH`. The renderer uses a temporary database and creates eight `/tmp/fish-*.html` files.
