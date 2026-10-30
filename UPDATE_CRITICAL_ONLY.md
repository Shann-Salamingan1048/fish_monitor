# Critical-only automatic starts and GUI unit choice — 23 October 2026

Warnings stay visible but do not initiate automatic pump or fan correction. Prediction alerts also do not operate actuators. Actual critical readings must meet the existing confirmation rule. Saved custom critical thresholds are respected.

| Input | Warning: no new correction | Critical: starts correction |
|---|---|---|
| Temperature | At/above warning but below critical | At/above critical: fan AUTO starts after 5 seconds. Default critical is 35°C / 95°F. |
| pH | Outside warning limits but inside critical limits | At/below critical low or at/above critical high: both pumps after 5 seconds. Defaults are pH ≤6.0 or ≥9.0. |
| Turbidity | Slightly Cloudy or Cloudy | Very Cloudy: both pumps after 5 seconds. |
| Water level | Low | Very Low: refill immediately. Overflow: extract immediately. |

Very Low and Overflow are now shown as Critical in both interfaces. Previously they were incorrectly labeled Warning despite starting immediate correction. Very Cloudy now consistently displays as Critical; category labels are not presented as measured NTU values. Legacy numeric turbidity packets use the configured warning and critical thresholds.

## Recovery behavior

The change gates **starting** correction. Once a critical condition has started a correction, existing recovery rules still apply:

- pH correction continues until pH returns inside the warning boundaries.
- Turbidity correction continues through Cloudy/Slightly Cloudy until Clear or Very Clear.
- A refill started at Very Low continues through Low until Normal. Low alone does not start a new refill.
- Low prevents continued draining during an active critical quality correction and selects refill only; overflow selects extraction only. Standalone overflow extraction stops if the level drops to Low without starting a new refill.
- Fan cooling retains its 0.5°C / 0.9°F recovery margin and confirmation timing. A warning reading alone cannot switch an idle automatic fan on.
- A return to warning during the five-second start confirmation resets that confirmation. Manual overrides, explicit stops, shutdown and lost-heartbeat protection remain available.

## GUI temperature selection

Choose **Controls & settings → Display → Temperature unit → Celsius (°C) / Fahrenheit (°F)**. The existing desktop header selector works too. Both choices update each other, save to the database, and immediately update displayed temperatures, alerts and the open settings form. Changing display units does not change sensor calibration or control thresholds. Web preferences remain independently selectable per browser.

## Installation

If you already installed the shutdown/nonblocking release, replace the Pi project files and upload the updated **OUTPUT Nano** sketch. The input sketch is unchanged from that release.

If upgrading from an earlier version, update the Pi and **both Nanos** using UPDATE_SHUTDOWN_AND_UNITS.md. Keep the existing `fish_tank_state.db`. Restart AquaPi once and refresh the browser.

## Verification

112 Python tests passed, including sustained warnings, exact critical boundaries, interrupted confirmation, configurable critical thresholds, level severity, recovery behavior, GUI unit synchronization and Fahrenheit-to-Celsius limit conversion. Output firmware simulation passed warning rejection, critical confirmation, recovery and prior shutdown/pump/feeder tests. The actual Arduino Nano output build passed: 15,468 bytes program storage and 929 bytes static SRAM. Python and JavaScript syntax checks passed. Physical tank and graphical desktop checks still require your hardware.
