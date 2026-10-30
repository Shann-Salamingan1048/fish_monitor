# Two-point pH calibration — 23 October 2026

The INPUT Nano now uses one straight-line calibration through your existing acidic and alkaline measurements:

| Buffer pH | Recorded ADC |
|---|---:|
| 4.01 | 1006.67 |
| 9.18 | 839.33 |

The calculation is:

`pH = 4.01 + (ADC - 1006.67) × (9.18 - 4.01) / (839.33 - 1006.67)`

The pH 6.86 point is removed from the equation and startup calibration report. You can use that buffer as an independent check. With the recorded ADC 919.11, the new line gives approximately 6.7152 (displayed as 6.72), whereas the prior three-point curve forced it to 6.86. This is the expected mathematical difference between these calibrations, not a new measurement or a guarantee of improved accuracy. Recheck the buffers on the same Pi/power arrangement before resuming automatic pH control. If recalibrating physically, replace PH_ACID_ADC and PH_ALKALINE_ADC with newly stabilized measurements from their matching buffers; no arbitrary pH offset is added.

The full sketch still uses A1, DEFAULT analog reference, 15 timed samples and the trimmed mean of the middle nine. The nonblocking sampling, 500 ms reporting, temperature correction, sensor modes and shutdown/heartbeat behavior are retained. Outside the two reference points, the formula extrapolates; the Pi continues rejecting invalid readings outside its supported pH range.

## Installation

For a system already using the previous critical-only/shutdown release, upload only:

`arduino/Nano_INPUT_Millis/Nano_INPUT_Millis.ino`

No Pi or OUTPUT Nano change is required for this calibration update. Restart the updated Pi application after uploading, because it must arm the input Nano and keep sending heartbeats. Startup reports `PHCAL:NANO_2POINT_V1`, `PH4_ADC:1006.67` and `PH9_ADC:839.33`; there is no PH6_ADC field. The existing pH and pHADC packet fields are unchanged.

If upgrading from older firmware without the shutdown protocol, follow the Pi + both Nanos installation in UPDATE_SHUTDOWN_AND_UNITS.md first.

## Verification

The host simulation passed both endpoints, midpoint, intermediate values on both sides of the former middle point, the paired pH/pHADC serial report and all existing input timing/masking/shutdown tests. Actual Arduino Nano compilation passed: 11,668 bytes program storage; 694 bytes static SRAM. Physical buffer measurements remain to be checked on your setup. Previous Python and output-firmware results are documented in VALIDATION.md; those components did not change in this update.
