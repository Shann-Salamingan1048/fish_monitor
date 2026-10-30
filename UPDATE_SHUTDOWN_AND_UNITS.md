> Calibration update: the current INPUT sketch now uses the two-point formula in UPDATE_TWO_POINT_PH.md. Three-point descriptions and input build sizes below document the earlier shutdown release.

# Shutdown, nonblocking input and temperature units — 23 October 2026

## Install this release as a matched set

1. Stop AquaPi and back up the current project and `fish_tank_state.db`.
2. Update the Raspberry Pi project from this folder, including the new `temperature_units.py`, templates and static files. Keep your existing database and environment settings. No database is supplied in this ZIP.
3. Upload `arduino/Nano_INPUT_Millis/Nano_INPUT_Millis.ino` to the **INPUT Nano**.
4. Upload `arduino/NANO_OUTPUT/NANO_OUTPUT.ino` to the **OUTPUT Nano**.
5. Start the updated Pi application once with `./start_local.sh` (or your existing `python3 main.py` command). Refresh the browser. Both sketches use 115200 baud.

Both firmware updates are required. These instructions replace the older release notes saying to upload only the output Nano. Each sketch stays in its own Arduino folder. Select your actual Nano processor/bootloader when uploading.

Startup identifies `INPUTFW:ASYNC_V1` and `SYSTEMCTRL:V1`. The output still advertises `FEEDER:V3,FANCTRL:V1`, with the added `SYSTEMCTRL:V1` capability. Both boards start in standby and the updated Pi explicitly sends `SYSTEM:ON`. Opening Serial Monitor alone no longer operates the system. For a bench test, send the appropriate board prefix plus `SYSTEM:ON`, then `HEARTBEAT:1` at least once per second; otherwise the four-second timeout stops it.

## Off behavior

“System off” means closing the desktop application, Ctrl+C, a normal SIGTERM/Pi shutdown, or loss of the Pi's heartbeat. Closing a browser tab alone does not shut down the aquarium controller.

- On normal application exit, the Pi inhibits further hardware writes before sending the stop commands. Sensor updates, scheduler work and web/manual requests cannot turn actuators back on during shutdown.
- Both water pumps and the cooling fan switch off. An active feeder is cancelled and commanded closed; its PWM signal is disabled after a one-second closing interval. Camera pan/tilt PWM is disabled immediately. The Pi camera capture is stopped, and the input Nano stops acquiring measurements.
- The Pi sends each Nano a heartbeat every second. Without it, each powered Nano enters standby after four seconds. The output shuts down its actuators; the feeder then has its one-second closing interval. An ordinary heartbeat cannot re-arm a stopped board. Restart the app to resume. The Pi latches its command gate off if it receives an output `HOST_TIMEOUT` report.
- These are control-output stops, not electrical isolation. Sensors, the driver and servo power rails can remain powered. A Nano that loses its own power cannot execute a closing command or disable an independently powered PCA9685. Guaranteed all-power-off behavior needs a hardware power switch/interlock; software alone cannot provide it. Servo PWM off also removes holding torque and does not prove the gate physically closed.

The working D8 → IN1 extraction and D9 → IN3 insertion mapping and active levels are retained. Keep the repaired common ground connection.

## Input Nano changes

The attached `Nano_INPUT_Millis(1).ino` is the source for calibration and the temperature correction. Preserved values are pH 4.01 → ADC 1002.11, pH 6.86 → ADC 919.11 and pH 9.18 → ADC 839.33, on A1 with DEFAULT analog reference. The −0.2°C correction is applied only after validating the DS18B20 result, so a disconnected probe still produces ERROR.

The old outer `millis()` checks did not make the sensor routines nonblocking. Each pH batch waited about 300 ms, turbidity waited 100 ms, and a default 12-bit DS18B20 conversion could wait about 750 ms. When due together these waits could occupy roughly 1.15 seconds, delaying commands and float reads.

The new sketch starts temperature conversion and reads it after its conversion window, while servicing other work in between. It collects one analog sample at each scheduled time, keeps the same 15-sample pH trimmed mean and 10-sample turbidity mean, and retains the turbidity categories. An initial ADC conversion is discarded when changing analog channels. Sensor enable/disable and the existing pumping-mode pauses remain: PAUSED is an intentional control policy, separate from blocking the CPU.

There are no heap-allocated `String` objects in the input sketch. Text constants and command strings use flash storage, values use numeric variables, and a fixed 256-byte packet buffer sends a coherent snapshot in small UART chunks only when space is available. RX work is bounded so a continuous serial stream cannot starve sampling. Timers handle `millis()` rollover. OneWire and ADC transactions still take short CPU time; “nonblocking” here removes the long waits, not every hardware transaction.

Use the normal three-wire DS18B20 arrangement (VDD, data and GND, with the required data pull-up). Parasite-powered probes need suitable strong-pull-up hardware during conversion.

## Timing recommendation

Keep **500 ms serial reporting** for this system. At most 256 bytes per report is roughly 512 bytes/second, or about 4.4% of a 115200-baud 8N1 UART's payload capacity, before small status/command traffic. Reducing reports to every 1.5–2 seconds adds avoidable delay to float-based pump decisions. Reporting a cached value does not require resampling its sensor.

| Task | Interval |
|---|---:|
| Float sampling | 250 ms |
| Sensor packet | 500 ms |
| pH batch start | 2 seconds |
| Temperature conversion start | 2 seconds |
| Turbidity batch start | 2.5 seconds |
| Pi heartbeat | 1 second |
| Missing-heartbeat shutdown | 4 seconds |

These are scheduled intervals, not hard real-time end-to-end guarantees. USB, operating-system scheduling and sensor transactions add latency.

## Interface changes

The web header's receiving/unavailable/stale badge and the desktop footer's receiving/stale announcement are removed. Stale readings still cannot be plotted as fresh data or presented as a healthy alert summary. Individual ERROR, WAITING, PAUSED and DISABLED readings still communicate their actual state.

Choose **Temperature → Celsius (°C) / Fahrenheit (°F)** in the web header or the desktop header. The browser remembers its own choice; the desktop remembers its choice in the existing database. Viewers can choose display units without admin access. Readings, chart axes, temperature alerts, sensor-control readings, cooling-margin text and editable temperature limits use the chosen unit. Threshold entries convert back to Celsius when saved, so 86°F remains the same control threshold as 30°C. Serial packets, histories and all control logic stay in Celsius.

## Validation and hardware check

Both actual Nano builds passed with Arduino AVR core 1.8.8, OneWire 2.3.8, DallasTemperature 4.0.6, Adafruit PWM Servo Driver 3.0.3 and Adafruit BusIO 1.17.4.

| Sketch | Program storage | Static SRAM |
|---|---:|---:|
| Attached original input | 14,014 bytes | 710 bytes |
| Optimized input | 11,876 bytes | 694 bytes |
| Updated output | 15,468 bytes | 929 bytes |

Nano SRAM capacity is 2,048 bytes. The optimized input leaves 1,354 bytes beyond static storage for stack/runtime needs. The original's static figure excludes its runtime String allocations; compiler memory reports do not prove worst-case stack headroom.

Software checks cover shutdown/write races, correct startup/heartbeat routing, control rejection after off, output timeout and servo PWM stops, partial UART input, congested UART output, sensor cancellation, rollover, calibration, probe-disconnect handling, and Celsius/Fahrenheit displays and limit conversions. No physical hardware was available.

On your hardware, verify that both pumps run as before, normal app exit switches both pumps/fan off, the feeder closes, and removing the Pi link while keeping the output Nano powered stops actuators after four seconds. Restart AquaPi and confirm fresh input readings. Compare the pH buffers with the retained calibration and confirm a 30°C limit displays as 86°F without changing fan behavior. Keep a manual power cutoff available during the check.
