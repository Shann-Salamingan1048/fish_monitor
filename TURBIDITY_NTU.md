# Turbidity: 100-sample ADC and serial NTU

Upload `arduino/AquaPi_Mega/AquaPi_Mega.ino` to the Mega 2560. This is still one combined input/output sketch. Turbidity analog OUT stays on A0; use the module's analog mode. No Python or GUI changes are needed for this update.

## Sampling and serial output

- Collect 100 samples, one every 5 ms, without `delay()` or a 100-element array. Acquisition takes at least 500 ms and can take longer when other services run. There are no catch-up bursts after a delayed loop.
- Use a 32-bit running sum: 100 full-scale readings total 102300, which would overflow a 16-bit sum.
- Start a new batch about every 2.5 seconds, preserving the existing acquisition interval. Serial telemetry continues at 500 ms and repeats the most recent completed reading. The shared ADC finishes its current batch before starting pH or turbidity; it does not interrupt the other sensor halfway through its batch.
- Commands, float checks, feeder timing, temperature conversion and safety checks continue between ADC samples. Disabling the sensor, changing sensor mode or turning the system off discards an unfinished batch.
- Lower ADC means cloudier water: ADC 0 is in the default Very Cloudy range; higher ADC means clearer water. Existing ADC-based custom ranges and fail-safe rules remain in use.

After each completed batch, a separate diagnostic frame is printed at 115200 baud. For example, before NTU calibration:

```text
MegaInputStatus,TURBIDITY:LIVE,rawAdc:900.00,NTU:UNCALIBRATED,SAMPLES:100,NTU_METHOD:LINEAR_ESTIMATE
```

`rawAdc` is the average of the 100 raw ADC counts, before any NTU conversion, with fractional counts retained. It is not a single instantaneous sample. The normal `MegaInput` frame still includes integer `TurbidityADC` for the existing application and Developer calibration page. The diagnostic frame is separate to avoid enlarging that packet. Neither NTU nor the diagnostic is added to the GUI.

## Enter measured NTU references

The supplied generic module description advertises 0–1000 NTU, but provides no transfer curve. That range does not establish that ADC 0 equals 1000 NTU or that a particular clear-water ADC equals 0 NTU. The code therefore does not fabricate endpoints or apply another sensor model's voltage formula.

1. Use at least two reference solutions with known NTU values, or compare the same samples against a calibrated reference instrument. Use values within the stated 0–1000 NTU range. Visual labels such as “clear” and “cloudy” alone do not establish NTU.
2. Keep the supply, probe position and measurement conditions consistent. Let each reading settle, and record its printed `rawAdc` average. Do not use the first transient reading after moving the probe.
3. In the sketch, locate `TURBIDITY_NTU_POINTS`. Replace each `{-1.0f, -1.0f}` placeholder with `{measured_ADC, known_NTU}`. These are independent of the Developer page's clarity ranges; that page does not configure NTU reference points.
4. Order points from lowest NTU/highest ADC to highest NTU/lowest ADC. You may enter 2–8 points. ADC values must strictly decrease and NTU values must strictly increase. Each ADC must be in 0–1023 and each NTU in 0–1000.
5. Upload the sketch. `NTU` then prints a numeric **linear estimate** between adjacent reference points. More well-spaced measured points can represent a nonlinear response better than two endpoints. Validate the result against independent references before using it as a quantitative measurement.

`UNCALIBRATED` means missing or invalid reference points. `OUT_OF_RANGE` means the measured ADC lies outside the calibrated interval; the code does not extrapolate or clamp it to a misleading endpoint. Calibration lives in the sketch constants; this update does not write EEPROM. The number of displayed decimal places is formatting, not a claim of measurement accuracy.

## Verification

The Mega AVR build, all three C++ firmware simulations and the Python test suite are documented in `VALIDATION.md`. The default uncalibrated build uses 3509 bytes static RAM, 11 bytes more than the preceding release. Entering reference points can change compiler optimization and memory use. Hardware timing, electrical interference and measurement accuracy still require testing on the actual equipment.
