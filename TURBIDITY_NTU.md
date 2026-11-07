# Turbidity: ADC and NTU calibration

## Developer option

The web Developer option shows **Turbidity ADC** and **Turbidity NTU** together. ADC is the integer average reported by the Mega after each 100-sample batch. NTU is a piecewise linear estimate calculated on the Pi from your reference points. It displays **Not calibrated** until references are saved, and **Outside reference range** when ADC is outside those references. Stale, disabled or paused data is not displayed as a fresh measurement.

Update the Python application and web assets, then restart `main.py`. **No new Mega upload is needed if the previous combined Mega firmware is already installed.** The single `.ino`, pin assignments, sampling schedule and EEPROM behavior are unchanged. Keep your existing database when updating; it contains your settings, schedules and calibration.

1. Enable Developer option in `main.py`, start AquaPi, sign in as Admin, and open **Developer option**.
2. Click **Start calibration**. Water-level protection stays active; pH and turbidity cannot start water replacement while calibrating.
3. Choose the **Range unit**:
   - **ADC:** enter whole-number clarity ranges spanning 0–1023. Both endpoints are included; the next minimum is the previous maximum + 1. Lower ADC is cloudier, higher ADC is clearer. Existing ADC calibrations still work.
   - **NTU:** enable reference calibration and custom ranges. Enter 2–8 samples with known NTU, using **Use live ADC** after each reading settles, or enter the ADC manually. Enter clarity ranges covering 0–1000 NTU. Each maximum equals the next minimum; a shared boundary belongs to the higher range, and 1000 is included in the final range. Reference values and aquarium NTU thresholds are not prefilled.
4. Give each range a display name and clarity category. Use **Add range** for more rows. The category determines alerts and correction.
5. Click **Save calibration**, check the readings, return the probes to the aquarium, then click **Finish calibration**.

You can enable NTU reference calibration for the numeric display while keeping ADC as the range unit. Switching units keeps separate unsaved range drafts on that page; it does not reinterpret ADC numbers as NTU. Save the desired mode before leaving.

Quantitative NTU needs known-NTU reference solutions or samples measured with a calibrated reference instrument. The appearance “clear” does not establish 0 NTU. References must be within the existing 0–1000 NTU range, with ADC decreasing as NTU increases, at least one ADC count between neighboring references and at least 20 counts across the reference span. Two decimal places are display formatting, not measurement accuracy.

## Local Mega protection

Calibration is stored in the Pi database. Saving or loading NTU ranges converts them once into integer ADC ranges. The same converted boundaries determine web labels and the Mega's local clarity categories. The existing `MegaConfig` protocol sends these ranges to Mega RAM. Automatic decisions do not wait for Python to calculate an NTU value every sample.

Outside the reference ADC span, numeric NTU is withheld. In NTU mode, ADC below the cloudiest reference uses the highest-NTU range's category; ADC above the clearest reference uses the lowest-NTU range's category. Set those extreme categories appropriately for your tested setup. ADC mode always uses its configured ADC ranges.

Cloudy and Very Cloudy can start correction after the configured confirmation period. Slightly Cloudy can continue an existing correction; Clear and Very Clear end it. Calibration mode, sensor disabling, float priority, pH settling and System Off retain their existing behavior. Configuration is resent after reconnection; the Mega retains its existing heartbeat-stop behavior on communication loss.

## Sampling and serial diagnostic

The unchanged sketch collects 100 samples, one every 5 ms, using a 32-bit running sum and no sample array. A batch takes at least 500 ms; batches start about every 2.5 seconds. Serial telemetry repeats the latest completed result at 500 ms. Commands, float checks, feeder timing and other services continue between samples.

A separate diagnostic can still print:

```text
MegaInputStatus,TURBIDITY:LIVE,rawAdc:900.00,NTU:UNCALIBRATED,SAMPLES:100,NTU_METHOD:LINEAR_ESTIMATE
```

**Serial diagnostic NTU is separate from web NTU calibration.** It uses `TURBIDITY_NTU_POINTS` constants in the sketch and can still say `UNCALIBRATED` after web calibration. Web calibration does not change those constants or write EEPROM. The normal telemetry's integer `TurbidityADC` is used for web NTU and local rule mapping; the diagnostic prints the fractional mean. Use Developer option for the web-calibrated NTU value.

For standalone serial NTU, enter measured `{ADC, NTU}` reference pairs in the sketch constants and upload. Those constants do not change the web profile. Neither path assumes that ADC 0 equals 1000 NTU.
