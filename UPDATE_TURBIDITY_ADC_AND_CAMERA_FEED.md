# Turbidity raw ADC and camera feeding cycles

## Changes

- The input Nano reports `TurbidityADC` alongside the existing turbidity category. This is the integer average of the same ten ADC samples used for the clarity calculation (0–1023), before scaling. The clarity thresholds are unchanged.
- Raw ADC is printed in the Nano serial packet, for example `TurbidityADC:500`. It is not displayed in the web dashboard or desktop GUI. The serial field reports `WAITING`, `DISABLED` or `PAUSED` when applicable.
- The camera page has a Feeding cycles input beside Feed now. It defaults to 1 and sends the chosen positive whole number to the existing feeder endpoint. Admin access, feeder availability, busy state and pump interlocks still apply.
- The camera page shows feeding request feedback and feeder status locally.

## Install

1. Back up your current project and database, then stop the AquaPi application.
2. Update the Raspberry Pi project files from this archive. Preserve your existing database, credentials and local configuration. For this update the changed runtime files are `config.py`, `sensor_control.py`, `tkinter_gui.py`, `static/script.js`, `static/style.css`, and the three HTML files in `templates/`.
3. Upload `arduino/Nano_INPUT_Millis/Nano_INPUT_Millis.ino` to the **input Nano** using your existing board, processor and port settings. The output Nano sketch does not need an update for these changes.
4. Restart the application and refresh the web page. Sign in as Admin, open Camera, enter a cycle count and select Feed now.

The serial ADC field reports `WAITING` until acquisition finishes. Older input firmware still supplies its existing category normally.

## Verification

- 127 Python tests passed, including ADC transport to the API, invalid/missing ADC values, sensor toggles, reconnects and stale configuration rejection.
- Browser DOM checks passed on dashboard, graphs and camera, including custom cycle payloads, invalid counts, Admin access and busy/pump button states.
- Host C++ input firmware simulation passed, including ADC endpoints, sensor masks/modes and serial packet buffer bounds.
- Physical Nano, pump and camera hardware was not available for testing. The host firmware simulation is not an AVR compilation or hardware test.
