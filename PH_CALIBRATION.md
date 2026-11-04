# Selectable pH calibration

Upload the included Mega sketch once for this update. Replace the Python/web files, keep the existing database, and restart AquaPi. Subsequent calibration changes need no upload.

In **Developer option → Start calibration**, enable **Use saved pH calibration**:

| Method | Buffers |
|---|---|
| 2 points | 4.01 and 6.86 |
| 2 points | 6.86 and 9.18 |
| 2 points | 4.01 and 9.18 |
| 3 points | 4.01, 6.86 and 9.18 |

Enter each buffer's stable live **pH ADC** reading. Save, verify in the buffers, return the probe to the aquarium, then finish calibration. Values are retained on the Pi and sent to Mega RAM on connection. Existing saved profiles still work.

## Formula

For any selected pair, with buffer values p1/p2 and measured ADC values a1/a2:

`pH = p1 + (filteredADC - a1) * (p2 - p1) / (a2 - a1)`

The Mega computes the slope only when configuration changes. Reading-time conversion uses one subtraction, multiplication and addition.

For three points, use the 4.01–6.86 line on the acidic side and the 6.86–9.18 line on the alkaline side. Both meet at the measured 6.86 reference; there is no polynomial fit. Outside the selected buffer span, the end line is extrapolated, so verification is strongest inside the calibrated span.

## Noise filtering

- Collect 15 samples at least 20 ms apart without delay loops.
- Discard the three lowest and three highest; average the middle nine.
- Expose that averaged ADC for calibration; it avoids the extra lag of the display filter.
- Smooth runtime ADC: `filtered += 0.20 * (averaged - filtered)` at each completed batch, about every 2 seconds. The first valid batch initializes the filter directly.
- Calculate pH on the Mega once; the Pi does not smooth it again.

More smoothing would slow real changes, so this update retains the existing filter and verifies its spike rejection and convergence. Calibration starts from fresh filter state after switching methods. Invalid/missing values, reversed ADC ordering and separations under 20 counts are rejected. ADC is expected to decrease as pH increases for the configured module.

Filtering reduces fluctuations; it cannot correct an inaccurate buffer measurement or an electrical fault. Enter measured ADC values, not the buffer pH numbers or example readings.
