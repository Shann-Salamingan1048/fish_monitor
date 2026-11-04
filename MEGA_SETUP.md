# AquaPi: one Arduino Mega 2560

This version uses one **Mega 2560** and one USB cable to the Raspberry Pi. The Pi still runs the GUI, web server, Cloudflare connection, camera video and feeding schedule. The Mega reads the sensors and drives the pumps, fan relay and PCA9685 servo controller.

## What this does—and what it does not fix

Automatic water and temperature decisions now run on the Mega, using local sensor readings. The Pi sends configuration and manual requests; it does not calculate each automatic pump command. This reduces serial round trips and removes the need for two USB serial connections.

Changing controllers does not guarantee a fix for the pump-triggered USB disconnections. Your keyboard and mouse also disconnected. Motor supply disturbances, ground-return problems or interference can still affect a Mega and the Pi. Keep motor and servo power separate from the controller's USB/5 V supply; share a proper ground reference without routing pump current through the Mega or its USB cable.

This is **local control with supervised enable**, not unattended standalone operation after the Pi disappears:

- On boot, outputs are off. AquaPi enables the Mega and sends the saved settings.
- Once enabled/configured, the Mega makes automatic decisions itself.
- Heartbeats arrive every second. After four seconds without one, the Mega stops pumps and fan, closes the feeder, then disables servo PWM after its existing one-second closing interval.
- System Off stops the whole board, whether the request targets the logical input or output role.
- The app reconnects after USB returns. Pumps stay in manual stop; choose Resume automatic control or a deliberate manual action. The fan stays OFF until you choose Auto/On again. Interrupted feeding is not replayed.
- Settings are stored in the Pi database and copied into Mega RAM. **No EEPROM writes were added.** A Mega power cycle needs the Pi to send its saved profile again.

## Wiring: power off before moving wires

### Sensor and actuator signals

| Device / signal | Mega connection | Notes |
|---|---|---|
| DS18B20 DATA / DQ | D4 | Keep its existing 4.7 kΩ data pull-up to 5 V; use normal three-wire power |
| DS18B20 VDD / GND | 5 V / GND | Check the probe's actual wire identification |
| Turbidity module analog OUT / AO | A0 | Use analog output; maximum input is the Mega's 5 V rail |
| PH-4502C PO / analog OUT | A1 | Do not use DO or the temperature output |
| Upper float switch | D7 and GND | INPUT_PULLUP; closed-to-ground = triggered |
| Normal float switch | D8 and GND | INPUT_PULLUP; closed-to-ground = triggered |
| Low float switch | D9 and GND | INPUT_PULLUP; closed-to-ground = triggered |
| Fan relay/module control IN | D5 | Existing active-HIGH setting; change COOLER_RELAY_ACTIVE_LOW only if your relay is active LOW |
| Pump driver IN1 — extraction | D22 | Extraction control signal |
| Pump driver IN3 — insertion | D23 | Insertion control signal |
| Pump driver IN2 and IN4 | GND | Explicit LOW for your existing direction mapping; do not leave floating |
| Module signal grounds | Common GND | Mega, sensor modules, PCA9685 and motor-driver logic need the same reference |
| Mega USB | Raspberry Pi USB | Only the Mega; disconnect old controllers |

Keep the sensor modules' supply voltage appropriate for their actual board. Never connect the 12 V pump supply to a Mega signal pin or 5 V pin. D22/D23 are control signals only; pump current must flow through the motor driver. The fan requires its relay/driver, not a direct GPIO connection.

### PCA9685 servo driver

The combined sketch uses an Adafruit-compatible PCA9685 at its default I²C address **0x40**. This conversion keeps that driver.

| PCA9685 connection | Connect to |
|---|---|
| SDA | Mega D20 / SDA |
| SCL | Mega D21 / SCL |
| VCC (logic supply) | Mega 5 V |
| GND | Mega GND and external servo-supply GND |
| V+ / servo power terminal + | Separate regulated supply suitable for your servos, normally 5 V |
| Servo power terminal − | External servo-supply GND |
| OE, if exposed and unused | GND to enable outputs; firmware disables individual PWM channels when off |
| Channel 0 signal / V+ / GND | Camera pan servo |
| Channel 1 signal / V+ / GND | Camera tilt servo |
| Channel 2 signal / V+ / GND | Feeder servo |

Do not power servo motors from the Mega's 5 V pin. VCC powers the PCA9685 logic; V+ powers the servos. Keep the existing servo pulse range (120–520 at 50 Hz) and mechanical limits unless you have measured different safe limits for your servos. System Off disables PWM; it does not physically disconnect the external supply.

Power the Mega through USB for this setup. Use the external buck for servo power rather than tying a second 5 V source onto the Mega's USB-powered 5 V rail. Both supplies' grounds still connect.

Official references:
- Arduino Mega pinout: https://docs.arduino.cc/resources/pinouts/A000067-full-pinout.pdf
- PCA9685 wiring and power: https://learn.adafruit.com/16-channel-pwm-servo-driver/hooking-it-up
- Wire timeout: https://docs.arduino.cc/language-reference/en/functions/communication/wire/setWireTimeout/

## Upload the combined sketch

1. Back up your working project and stop `main.py`.
2. Extract the ZIP. Open `fish_monitor/arduino/AquaPi_Mega/AquaPi_Mega.ino` in Arduino IDE.
3. This is one complete `.ino` file. Use the new sketch folder; do not retain an old project header or extra sketch tabs.
4. Install **Arduino AVR Boards** in Boards Manager. Select **Arduino Mega or Mega 2560**, processor **ATmega2560**, and the Mega's port.
5. Install these libraries in Library Manager: **OneWire**, **DallasTemperature**, **Adafruit PWM Servo Driver Library**, and its **Adafruit BusIO** dependency. Wire comes with the board core.
6. Upload with pump/servo power disconnected initially. Serial speed is **115200 baud**.
7. Close Serial Monitor before running AquaPi. One application should own the serial port.

## Update your existing Raspberry Pi project

1. Stop AquaPi and back up your existing folder.
2. Extract this release into a clean folder. Use its updated Python files, templates and static assets together; do not mix old protocol versions.
3. Preserve `fish_tank_state.db` (or your `FISHTANK_DB_FILE` path), environment settings, and camera/Cloudflare setup. Keep your preferred `config.DEVELOPER_OPTIONS` setting in the new `main.py`.
4. Upload the included single-file firmware, connect only the Mega and close Serial Monitor.
5. Start `python main.py` from the new project folder, using your existing Python environment if appropriate. A service must point to the new project location if it changes.

No additional Python dependency is required. The application has one Mega reader and no alternative two-controller mode.

The terminal should show `Mega identified (input + output).` and an `AquaPiMega,CFG:...` acknowledgement. Standard boards can appear as `/dev/ttyACM0`; CH340 clones can appear as `/dev/ttyUSB0`. Discovery follows the physical USB socket when the number changes. Keep that socket unchanged while the app runs.

If automatic selection finds another Arduino, disconnect that board or explicitly select the Mega:

```bash
FISHTANK_MEGA_PORT=/dev/ttyACM0 python main.py
```

For a headless service, use `python main.py --headless`. The only controller-port override is `FISHTANK_MEGA_PORT`; old input/output port and controller-mode environment variables are no longer used.

The protocol now consistently uses `MegaInput`, `MegaInputStatus`, `MegaOutput`, `MegaServo`, `MegaConfig` and `AquaPiMega`. Input and output share the same physical USB handle. Upload the new firmware and update the application together.

## Calibration and automatic behavior

**Recheck/recalibrate pH and turbidity after moving to the Mega.** Old ADC values may shift with the board's ADC reference and power supply. In Developer option, choose any two of your **4.01, 6.86 and 9.18** buffers, or choose all three. Do not reuse example ADC values as actual measurements.

The firmware's two ADC constants remain zero until you enter measured constants yourself or the Pi sends a saved web calibration. Zero constants deliberately mean uncalibrated, not pH zero. Web calibration, including the chosen 2/3-point method, is copied into RAM; the Mega then applies the pH formula locally. Three-point calibration uses two linear segments meeting at 6.86; each passes through its two measured buffers. Both formulas are in this firmware, so switching methods requires no further upload. The web uses that reported pH instead of independently filtering it a second time. Custom turbidity labels remain in the web. Developer option accepts ADC or NTU clarity ranges and shows both readings. NTU references and ranges are converted on the Pi into matching ADC ranges and clarity categories for the Mega; no firmware re-upload is required. See `TURBIDITY_NTU.md`.

Automatic operation:

| Condition in automatic mode | Local action |
|---|---|
| Overflow | Extraction until Normal has remained detected for 3 seconds; Low immediately takes priority |
| Low / Very Low | Insertion until Normal has remained detected for 3 seconds |
| Very Cloudy (critical category) for 5 seconds | Both pumps; continue through Cloudy/Slightly Cloudy until Clear/Very Clear |
| Cloudy warning alone | Does not start fail-safe |
| Critical pH for 5 seconds | Both pumps; stop quality correction once inside the warning range |
| After pumping, or initial configuration/calibration | Allow 120 seconds with Normal level and pumps off before pH can start another correction |
| Disabled quality sensor | Cannot trigger or sustain its own automatic correction |
| Calibration mode | Quality corrections paused; float protection remains available |
| Manual pump override | Runs the chosen manual action; automatic rules resume only when requested |

pH limits and critical temperature use your existing saved alert settings. The fan reads temperature locally in every pump mode, preserving its 5-second confirmation and 0.5 °C hysteresis. Temperature stays active unless explicitly disabled or the system is off. Feeder scheduling stays on the Pi, while the Mega executes the requested number of cycles without blocking sensors. Feeding is rejected/stopped during pumping as before.

## Optimization and validation

- Non-blocking DS18B20 conversion; cooperative ADC sampling and feeder timing.
- pH: 15 samples, discard the lowest/highest three, average the middle nine, then EMA smoothing.
- One bounded command parser and one frame-safe transmit queue. Congestion drops whole frames rather than mixing messages or waiting indefinitely for UART space.
- Fixed-size buffers; no runtime Arduino String construction or EEPROM calibration writes.
- A 2.5 ms Wire timeout bounds stuck I²C transactions on the AVR core.
- Turbidity averages 100 ADC samples, one every 5 ms, with a 32-bit running sum and no sample array. A batch takes at least 500 ms; batches start about every 2.5 seconds. Lower ADC means cloudier water. Existing integer clarity thresholds remain unchanged. See `TURBIDITY_NTU.md` for serial output and NTU calibration.
- 500 ms telemetry snapshots; sensor acquisition intervals remain independent.
- Atomic configuration staging: incomplete settings cannot enable automatic quality control.

See `VALIDATION.md` for the current Mega build size and validation results.

See `VALIDATION.md` for the current test results. Three C++ simulations test combined operation and local protection, including no Pi pump/temperature commands during automatic decisions, both pumps, float priority, confirmation, pH settling, outlier rejection, disabled sensors, calibration mode, packet boundaries, UART backpressure, heartbeat expiry, manual stops and System Off. Physical pump wiring and USB-noise behavior were not tested here.

Before restoring actuator power, verify each sensor/float display and each output's off state. Then test one actuator at a time, System Off, and USB reconnection. Finally test both pumps while watching the Pi's USB log. If USB still drops, the underlying hardware issue remains; automatic reconnection cannot repair it.

## Water-change schedule firmware

This release adds `WATERCHANGE:V1` with bounded local drain/refill phases. Upload the single combined sketch once to enable scheduled water changes from GUI or web. Pin assignments are unchanged. On completion automatic control resumes; stops and faults pause automation. See `WATER_CHANGE_SCHEDULE.md`.
