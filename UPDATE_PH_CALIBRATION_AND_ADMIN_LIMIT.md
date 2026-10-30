# AquaPi: prediction wording, Admin limit and reviewed input firmware

## What changed

- GUI and web: “Trend observation” is now **Readings to watch before predicting (seconds)**. At 30 seconds, AquaPi checks roughly the last 30 seconds of readings for a consistent rise or fall. It needs enough samples and a sufficiently consistent trend; a prediction is not guaranteed after exactly 30 seconds. This setting does not change the separate five-second critical-condition confirmation.
- “Alert duration” is now **Keep prediction visible (seconds)**. This controls prediction messages, not how long a real Critical alert remains active.
- Added **Maximum signed-in Admin devices**, default 2. Find it in GUI → Controls & settings → Alert settings, or web → Settings → Advanced settings. It is saved in the database. Lowering the limit immediately signs out the least recently active excess sessions. Tabs sharing cookies use one slot; different browsers/private windows use separate slots, even on one physical device. The total website-viewer and camera-viewer limits remain separate.
- Included the reviewed `Nano_INPUT_Millis(3).ino` as `arduino/Nano_INPUT_Millis/Nano_INPUT_Millis.ino`, using the confirmed buffers **6.86 and 9.18**. The output Nano is unchanged.

## Review of your input sketch

The two-point equation is correct:

`pH = 6.86 + (ADC - ADC686) × (9.18 - 6.86) / (ADC918 - ADC686)`

`ADC686` and `ADC918` must be measured from your own probe. Your sketch correctly stores actual captures in EEPROM instead of inventing a 6.86 ADC value from the old 4.01 calibration. Until a valid calibration exists, it sends `pH:WAITING`.

It takes 15 ADC samples, discards the three highest and three lowest, and averages the remaining nine. Its 0.20 exponential smoothing reduces variation but slows response: after 11 fresh batches it reflects approximately 91% of a sudden sustained change. With batches about two seconds apart, that is about 20–22 seconds, before any additional host filtering/confirmation. This is a mathematical filter estimate, not a measured probe response time. The user's 0.20 setting is preserved.

Serial output every 500 ms does not mean a fresh pH measurement every 500 ms. `pHADC` is the latest trimmed mean without the exponential smoothing; the displayed/control `pH` uses the smoothed ADC. Filtering reduces noise; it does not fix incorrect buffer measurements or a drifting power supply. Readings below 6.86 or above 9.18 extrapolate beyond the calibration interval, so verify them independently if they matter to your thresholds. Your 4.01 buffer can be a separate accuracy check without becoming a third calibration point.

Corrections to the upload:

1. Moved the `SensorBit` enum before the first function. Arduino's generated prototypes otherwise produced a Nano compile error, even though ordinary C++ host simulation compiled.
2. Cleared the stability history after every successful capture so the next capture/retry needs fresh readings.
3. Made `NANOINPUT,SYSTEM:OFF` stop calibration as well as normal acquisition. A deliberate `PHCAL:START` can still run pH-only calibration while normal monitoring is off.
4. Corrected the EEPROM-record comment to 14 bytes on the AVR Nano. The existing record format, checksum and magic value are preserved, so valid calibration from your uploaded sketch remains compatible.

## Install and calibrate

1. Stop `main.py` normally. If you set up automatic startup, stop that service too so it does not reopen the serial port. Keep the output Nano connected until normal shutdown has stopped the actuators. Back up the application folder and database.
2. Copy the updated application files over your working folder while keeping your existing database and environment settings.
3. Upload **only the INPUT Nano** sketch from `arduino/Nano_INPUT_Millis/Nano_INPUT_Millis.ino`. Use the Nano processor/bootloader option that works for your board. EEPROM comes with Arduino AVR Boards; OneWire and DallasTemperature are still the required sensor libraries. Do not upload the old 4.01-calibrated input sketch from an older release.
4. Use the same Pi-powered wiring and supply used during aquarium monitoring. Keep `main.py` stopped during calibration. Open the INPUT Nano port at **115200 baud**, with **Newline/LF** line endings. Do not open Serial Monitor and AquaPi on the same port together.

If using a Pi terminal with the project environment active:

```bash
python -m serial.tools.list_ports
python -m serial.tools.miniterm /dev/ttyUSB0 115200 --eol LF --echo
```

Replace `/dev/ttyUSB0` with the identified **input** Nano port. The number can change after reconnecting. Miniterm exits with Ctrl+] (Control plus the closing square bracket).

5. Send each command below as one line followed by Enter.

```text
NANOINPUT,PHCAL:STATUS
```

If it reports `CALIBRATED:1` with your verified ADC values from the same setup, the stored calibration loaded. You can skip recapturing unless the probe, supply or adjustment changed or the buffer checks fail.

To perform a fresh calibration, put the probe in your 6.86 buffer and send:

```text
NANOINPUT,PHCAL:START
```

Allow the probe to settle. Wait for repeated `PHCAL:LIVE` lines with `STABLE:1` and stable ADC values, then send:

```text
NANOINPUT,PHCAL:6.86
```

Expect `CAPTURED_6.86`. Rinse the probe between buffers according to its instructions, place it in 9.18 buffer, and wait for it to settle and report `STABLE:1` again. A fresh five-reading window is required. Then send:

```text
NANOINPUT,PHCAL:9.18
```

Expect `PHCAL:SAVED` with both ADC values. If you see `WAIT_FOR_STABLE_ADC`, wait and retry. If you see `INVALID_ADC_PAIR`, check the buffers/wiring and repeat the captures; invalid pairs do not replace the stored calibration. Stable ADC is a noise check, not proof of chemical accuracy.

Finish with:

```text
NANOINPUT,PHCAL:STATUS
NANOINPUT,PHCAL:STOP
```

6. Close the serial terminal and restart `main.py`. AquaPi arms the input board and resumes monitoring. Check the buffers to verify readings before returning the probe to the aquarium. A restart should keep the calibration in EEPROM. Preserve the Pi database to keep the Admin PIN, session limit, schedules and alert settings.

## Verification

- Actual Arduino Nano ATmega328P Old Bootloader target compiled with AVR Boards 1.8.8, OneWire 2.3.8, DallasTemperature 4.0.6 and built-in EEPROM 2.0.
- Flash: **14,230 / 30,720 bytes**. Static SRAM: **747 / 2,048 bytes**, leaving **1,301 bytes** for stack/runtime use. Static RAM figures are not a runtime stack-usage measurement.
- Input simulation passed: two calibration anchors/midpoint, missing calibration, stability gates, fresh captures, EEPROM reload and checksum rejection, trimmed outliers, EMA response, nonblocking sampling, UART backpressure, masks, shutdown and timer rollover.
- Output firmware simulation passed; its sketch is unchanged.
- 156 Python tests passed, including adjustable Admin capacity, concurrent logins, persistence, permission checks, invalid values and immediate session eviction when lowering the limit.
- Eight web pages passed DOM and rendered browser checks at phone/desktop widths. No hardware measurements or live Tk display test were performed.

Arduino references checked: [EEPROM update behavior](https://github.com/arduino/ArduinoCore-avr/blob/master/libraries/EEPROM/README.md), [Nano specifications](https://store.arduino.cc/products/arduino-nano).
