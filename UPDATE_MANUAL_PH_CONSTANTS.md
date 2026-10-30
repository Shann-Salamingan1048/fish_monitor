# Manual pH calibration constants — current input firmware

This supersedes the previous EEPROM calibration instructions. There are no EEPROM reads, writes, capture commands or automatic calibration saves in the current input sketch. Existing EEPROM contents are ignored; there is no need to erase them.

## Why hardcoding is sufficient

ADC values written in the sketch become part of the uploaded program and survive power loss. EEPROM is an optional convenience for changing calibration without editing/re-uploading the sketch. For identical ADC measurements, either storage method gives the same pH result. EEPROM does not improve accuracy or reduce noise.

## Enter your own measurements

Open `arduino/Nano_INPUT_Millis/Nano_INPUT_Millis.ino` and replace these two placeholders:

```cpp
constexpr float PH_ADC_686 = 0.0f;  // Your measured ADC in pH 6.86 buffer.
constexpr float PH_ADC_918 = 0.0f;  // Your measured ADC in pH 9.18 buffer.
```

The zeros deliberately mean “not entered.” They are not calibration values. Your new measured pair was not provided, so the older pH 4.01 ADC is not reused or relabeled as pH 6.86. With unset or invalid constants, raw ADC remains available but pH reports `WAITING`. Do not rely on pH protection until you have entered and verified both values.

Use stable raw-ADC measurements from the same probe, wiring and Pi power supply used during operation. Keep decimals in your averaged readings. After entering the values, upload the INPUT Nano again. No output Nano or Raspberry Pi code change is needed for this correction.

## Reading raw ADC yourself

Normal AquaPi serial packets include `pHADC`, the batch's trimmed mean before exponential smoothing. To measure buffers separately with `main.py` stopped:

1. Stop AquaPi normally and stop its auto-start service if one is configured. Keep the output Nano connected until normal shutdown has stopped the actuators. Use your normal Pi-powered input wiring.
2. Open the INPUT Nano serial port at 115200 baud with Newline/LF endings. Do not let AquaPi and Serial Monitor compete for the same port.
3. Send `NANOINPUT,PHRAW:START`. This only enables read-only pH measurements. It does not store calibration or activate the output Nano.
4. Put the probe in your 6.86 buffer, allow the readings to settle, and record/average several `pHADC` readings. Repeat with 9.18, rinsing between buffers according to the probe instructions.
5. Send `NANOINPUT,PHRAW:STOP`, close the terminal, put your measurements into the two constants and upload the input sketch.
6. Restart AquaPi and verify the readings against your buffers.

The raw mode runs without a Pi heartbeat, so it is suitable for a serial-terminal measurement session. `NANOINPUT,SYSTEM:OFF` also stops it. While raw mode is active, normal sensor/mode/ON commands are ignored. It stores no readings across restart.

If using the Pi terminal in the project environment:

```bash
python -m serial.tools.list_ports
python -m serial.tools.miniterm /dev/ttyUSB0 115200 --eol LF --echo
```

Replace `/dev/ttyUSB0` with your actual INPUT Nano port. Exit miniterm with Ctrl+].

## Filtering and formula

- Samples are taken without long delay loops: 15 ADC samples, about 20 ms apart, every roughly two seconds.
- The three lowest and three highest samples are discarded. The middle nine are averaged to reduce isolated spikes. This result is printed as `pHADC`.
- The pH reading uses exponential smoothing with `PH_EMA_ALPHA = 0.20f`. A sustained step reaches about 91% after 11 batches, roughly 20–22 seconds. This is the filter's mathematical response, not a measured probe response time. Increasing alpha reduces lag but allows more variation; 1.0 disables this second smoothing stage.
- The two-point line is anchored at 6.86. Its scale is calculated once at compile time, avoiding repeated division. No intermediate ADC rounding is performed.

```text
scale = (9.18 - 6.86) / (ADC918 - ADC686)
pH = 6.86 + (filteredADC - ADC686) × scale
```

The formula converts ADC into pH; it cannot remove electrical noise on its own. Filtering reduces fluctuations, while correct buffer measurements determine calibration accuracy. Values outside 6.86–9.18 extrapolate beyond the measured interval. Verify them separately when relevant to your alarm limits. The code retains the prior guard for this sensor setup: ADC decreases with rising pH, neither anchor is at an ADC rail, and the anchors are at least 20 counts apart.

## Verification

- Host firmware simulation passed with unset constants, a synthetic valid 900/800 ADC pair, equal anchors, reversed anchors and a rail value. Synthetic measurements are only in temporary test builds, never the distributed sketch.
- Tests cover anchors/midpoint, pH unavailable before constants are entered, raw reporting, rejection of three high/three low outliers, EMA response, nonblocking acquisition/transmit, sensor masks, stop/heartbeat behavior and timer rollover.
- Actual Nano ATmega328P Old Bootloader builds passed with AVR Boards 1.8.8, OneWire 2.3.8 and DallasTemperature 4.0.6. Unset sketch: 12,152 bytes flash. Synthetic configured build: 12,398 bytes flash. Both: 703 bytes static RAM, leaving 1,345 bytes for runtime/stack. Actual constants can slightly change the compiled size.
- Raspberry Pi application, web UI and output firmware are unchanged from the previous release. Their previous test results are historical; they were not rerun for this firmware-only correction.
- No physical probe accuracy measurement was performed.

To repeat the input simulations: `python tests/run_input_firmware_tests.py` (development C++ compiler required).
