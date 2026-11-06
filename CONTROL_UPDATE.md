# AquaPi control update

## Install once

1. Stop AquaPi and keep a backup of your project and SQLite database.
2. Replace the application files with this package; keep your existing database.
3. Upload `arduino/AquaPi_Mega/AquaPi_Mega.ino` to the Mega 2560 once. Pins and baud rate are unchanged.
4. Close Serial Monitor, restart `main.py`, and select automatic water control and Auto fan mode.

## What changed

- Low and Very Low now start refilling. Overflow starts draining. Both corrections continue for 3 seconds of continuous Normal detection. An opposite water-level limit immediately overrides that allowance.
- Scheduled refills use the same allowance; their refill timeout still takes priority.
- Temperature acquisition, display and automatic cooling continue during pumping. Auto cooling uses the configured critical temperature, 5-second confirmation and 0.5°C cooling margin.
- Quality correction starts on confirmed Critical pH, Cloudy, or Very Cloudy. Warning pH and Slightly Cloudy alone do not start correction. Both pumps run until pH is back inside the warning range and/or clarity is Clear/Very Clear, depending on which causes triggered. Low/Overflow retain pump-direction priority. Disabling the triggering sensor or stopping the system cancels its correction.
- Water-change schedules skip busy occurrences. Due feeds wait for pumps to finish, even beyond 30 minutes. Confirmed not-started pump conflicts are postponed; uncertain delivery or interrupted servings are not replayed.
- The two requested explanatory notices have been removed. The web calibration page has method and buffer-pair selectors.

## Switch pH calibration from the web

Open **Developer option → Start calibration → Use saved pH calibration**.
Choose **2 points**, then select **4.01 / 6.86**, **6.86 / 9.18**, or **4.01 / 9.18**. Choose **3 points** to use all three buffers. Only the selected ADC fields appear.
For each buffer, enter its stable live pH ADC reading. These readings are already averaged; do not enter pH numbers as ADC values.
Save, verify against the buffers, return the probe to the aquarium, then finish calibration.

The Pi keeps your calibration and sends it to Mega RAM after connection. No EEPROM writes or repeat firmware uploads are needed for changing method or values. Existing two-point profiles still load. Three-point fitting uses two straight-line segments through the three references; accuracy still depends on valid measurements and buffer condition. The existing pH settling delay remains in effect after pumping.

For schedules, see `WATER_CHANGE_SCHEDULE.md`. Software checks cannot establish the volume moved in 3 seconds; verify the allowance with your installed pumps and float positions.
