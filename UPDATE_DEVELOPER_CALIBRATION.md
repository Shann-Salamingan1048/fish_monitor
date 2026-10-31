# AquaPi: Developer option and pH settling — 31 October 2026

This update applies pH/turbidity calibration on the Raspberry Pi (or your Zorin laptop). It uses the ADC fields already sent by the current INPUT Nano. It does not write Nano EEPROM, change Nano firmware, or require unplugging/rearranging wiring.

## Install

1. Stop `main.py` normally, or stop the service that runs it. This lets the existing shutdown sequence stop the hardware.
2. Back up your existing project, especially `fish_tank_state.db`. Copy the updated project files into the same project directory. **Keep your database**, environment settings and service configuration. No database is bundled in this ZIP.
3. Start with your normal command, for example `python main.py` or `python main.py --headless`. No new Python or Arduino dependencies are required by this update.
4. Sign in as Admin on the web. Open **Developer option** in the navigation.

The currently installed input sketch must send `pHADC:` and `TurbidityADC:` in normal `NANOINPUT` packets. The latest included input sketch already does this and is unchanged. If those fields are missing from your installed firmware, web calibration cannot invent them: that older INPUT sketch would need one update. Do not reflash either Nano just for this release when these fields are already present.

## Enable or hide the page

Near the top of `main.py`:

```python
config.DEVELOPER_OPTIONS = True
```

The ZIP ships with the line enabled so you can calibrate. To hide the feature, finish calibration, stop AquaPi, comment out that line, and restart:

```python
# config.DEVELOPER_OPTIONS = True
```

Commenting it out removes the navigation entry and makes `/developer` and its API unavailable (404). Uncomment and restart to enable it again. The page and its API require Admin access even when enabled. There is no hidden circle/button.

Hiding the page **does not erase or disable saved calibration**. Both web and desktop readings and automatic decisions continue using the saved values. Settings are kept in the Pi database, not in the Nano. Keep that database when moving from laptop to Pi if you want to carry over settings; verify readings with the Pi's actual power supply and wiring before relying on a laptop calibration.

## Calibrate pH using 6.86 and 9.18

1. Ensure the pH sensor is enabled in **Sensors**. Use normal automatic control, with the water level Normal and pumps stopped, so ADC sampling is active.
2. Open **Developer option → Start calibration** **before** moving the probe into a buffer. This pauses pH/turbidity-triggered correction. It stops an existing quality correction, while preserving any active level protection. Enabled float switches and temperature protection continue working.
3. Place the pH probe in the **6.86** buffer. Follow the probe/buffer manufacturer's rinsing and handling instructions. Wait until the live **pH ADC** is steady, typically allowing 1–2 minutes initially and longer if readings are still drifting. Record an average of several stable displayed ADC readings.
4. Repeat in the **9.18** buffer. Use the value on your buffer label at the calibration temperature; this page is specifically for the requested 6.86/9.18 pair.
5. Tick **Apply calibration on this Pi**, enter the two ADC values, and click **Save calibration**. Enter ADC numbers, not `6.86` and `9.18` as the ADC values. No example ADC pair is supplied as your calibration.
6. Check both buffers against the pH reading shown by AquaPi. The display is smoothed, so allow it to settle after changing solutions. The web/GUI use Pi-calibrated pH; the Nano's own serial `pH:` field still reflects its existing code constants. `pHADC:` is the calibration input.
7. Return the probes to the aquarium, save any other changes, then click **Finish calibration**. pH-triggered correction waits for the settling period described below. Turbidity returns to its existing critical-confirmation rules.

The current Nano `pHADC` is already a trimmed mean (15 samples, discard the three lowest and three highest, average the middle nine). It is **before** the Nano's extra exponential smoothing. The Pi converts that ADC using:

```text
pH = 6.86 + (filteredADC − ADC686) × (9.18 − 6.86) / (ADC918 − ADC686)
```

The Pi smoothing has the same nominal response as alpha 0.20 every two seconds, weighted by elapsed time so repeated 500 ms packets do not make it respond four times faster. It resets on invalid/paused readings, reconnect or calibration changes. Calibration data must have ADC686 greater than ADC918 by at least 20 counts, matching the existing sensor's direction; reversed, identical, non-finite or rail values are rejected. This is not temperature compensation. Below 6.86 or above 9.18, the formula extrapolates; verify the aquarium's operating range rather than assuming identical accuracy everywhere.

If the pH checkbox is unticked and saved, AquaPi uses the Nano's original pH value again. The distributed sketch has placeholder constants, so that fallback remains WAITING until valid code constants are supplied. Leaving Pi calibration enabled avoids that requirement. A saved Pi calibration never falls back to old Nano pH when ADC data is missing.

## Custom turbidity names and ranges

1. Start calibration and enable the turbidity sensor. Use the live **Turbidity ADC** to establish your own measured clarity ranges. ADC counts are not NTU.
2. Tick **Apply custom ranges on this Pi**. Initially the editor shows ranges equivalent to the existing Nano thresholds, not laboratory calibration data.
3. Each row contains an inclusive **minimum ADC**, **maximum ADC**, **display name**, and **category**. Use **Add range** or **Remove** to change the number of rows.
4. Every whole ADC count from 0 to 1023 must belong to exactly one range. For example, a range ending at 170 must be followed by one starting at 171. Gaps, overlaps, repeated/reserved names and invalid values are rejected without changing saved settings. Up to 20 ranges are supported.
5. Save, verify your samples, return the sensor to the aquarium, and finish calibration.

| Category | Alert and automatic behavior |
| --- | --- |
| Very Cloudy | Critical; starts correction after the existing confirmation period. |
| Cloudy / Slightly Cloudy | Warning; does not start correction. An already-running turbidity correction continues. |
| Clear / Very Clear | Normal; ends the turbidity correction, subject to water-level protection. |

Your display name can be different from the category. For example, “Needs cleaning” can use category Very Cloudy. The category controls behavior; the name is displayed on web and desktop. A name containing numbers is still a label and is not plotted as a numerical NTU measurement. Unticking custom ranges and saving restores the Nano's original clarity classification.

## How the pH settling wait works

- After pumping, pH cannot start a new automatic correction until **both pumps are off** and an enabled normal-level switch reports **Normal continuously for 120 seconds**. For compatibility, older all-enabled float packets can provide the same Normal state.
- If pumping resumes or the reported level leaves Normal, the wait restarts. If Normal cannot be established (for example, the normal-level sensor is disabled), the countdown waits; it does not assume Normal.
- At the end of the wait, a **new** critical pH condition must still meet the existing five-second confirmation. Pre-wait critical samples do not count.
- Fresh pH readings continue to display and may still show an alert. The wait blocks new **pH-triggered pumping**, not the sensor's acquisition. The web and GUI explain when pH correction is waiting.
- Water-level protection and turbidity correction keep working during the wait. Manual overrides retain their existing behavior.
- An already-running pH correction continues using readings to decide when it has recovered; it does not pause merely because it started its own pumps. Once it ends, the wait prevents immediate restarting. A turbidity correction cannot hand straight over to pH without the wait.
- Finishing calibration also starts this settling requirement. `config.PH_SETTLE_SECONDS` defaults to `120.0` if you later need to adjust it after testing.

A fixed delay is a precaution, **not a measurement that the probe has stabilized**. Probe condition, temperature and mixing affect settling. The extra critical confirmation checks persistence, not calibration accuracy. Check behavior with your real aquarium hardware before relying on it unattended.

Calibration mode stays active if a browser closes or AquaPi restarts. A reminder appears in the web and GUI; it only ends when an Admin clicks Finish calibration. Finish before hiding Developer option. If you hide it while calibration is still active, re-enable the line and restart to finish; the program intentionally does not resume quality correction silently while a probe may still be in buffer.

## Verification

See `VALIDATION.md` for automated tests and limits. No live aquarium, physical probe, or Pi timing measurements were available. Both Nano sketches are unchanged from the previous ZIP.
