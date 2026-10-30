# Fan controls and sensor confirmation

## Install this update

1. Stop the pumps and feeder through the existing controls, then close the fish-monitoring application.
2. Back up the current project, database and working input-Nano sketch.
3. Copy the top-level Python files from this archive's `fish_monitor` folder into your existing project, including the new `fan_control.py`. Replace `static` and `templates` with the supplied versions. Keep your existing database and local environment settings.
4. Upload **only** `arduino/NANO_OUTPUT/NANO_OUTPUT.ino` to the OUTPUT Nano. Keep the existing PCA9685/servo libraries and board settings. The fan uses the existing cooler relay on D5; this release does not change relay polarity or wiring.
5. **Do not upload or replace your working INPUT Nano sketch for this update.** You recalibrated it while connected to the Pi, and those later ADC values have not been supplied here. The archived input sketch contains the last shared values, not necessarily your working calibration.
6. Restart the application and refresh the browser. The updated pages request asset version 10. Fan controls become available when the updated output Nano reports its capability and state.

No database migration or input-Nano upload is needed for this release. The tests folder is optional on the Pi.

## Fan controls

- Localhost, LAN and Cloudflare: expand **Access Mode**, sign in as Admin, and use **Cooling fan**.
- Desktop: **Controls & settings → Fan**.
- **Auto:** follows the configured temperature warning threshold. Requires five seconds of continuously hot readings to switch on; requires five seconds at or below threshold minus 0.5°C to switch off.
- **On / Off:** immediate manual overrides, independent of temperature. They remain selected until changed or until the Python app restarts. Off suppresses automatic cooling; choose Auto to restore it.
- Disabling the temperature sensor stops automatic cooling and its associated timeout behavior. Manual Fan On still works without the sensor.
- The application distinguishes requested mode from Nano-confirmed relay state. This confirms the relay command, not physical fan rotation; no tachometer is installed. The output Nano starts off, and the application restores its current requested mode on reconnection. A Python app restart selects Auto. An orderly shutdown requests fan Off. If the Pi disconnects while manual On is selected, the Nano keeps that manual setting until reset or another command.
- The existing automatic stale-temperature behavior remains: Auto turns the fan off after 15 seconds without valid temperature data.

## Five-second confirmation

A condition must persist, not an exactly identical number. For example, pH 9.1, 9.2 and 9.15 can all confirm an alkaline condition if they exceed the configured high threshold.

- pH below its low threshold or above its high threshold must persist for at least five seconds before starting a pH water-change action.
- Cloudy and Very Cloudy form the abnormal turbidity condition. It must persist for at least five seconds before starting extraction. Slightly Cloudy does not start extraction.
- Crossing from acidic to alkaline starts a new pH timer. Normal, invalid, paused or disabled inputs reset the corresponding pending confirmation. Manual pump changes, input reconnection and pH threshold changes also restart confirmation.
- A gap longer than three seconds between accepted packets/temperature updates breaks confirmation. No pump or fan starts solely because a background timer expires without further data.
- Water-level corrections and pump stops at the low, normal and upper switches remain immediate. This avoids delaying overflow correction and dry-run protection. Existing manual pump overrides retain their documented behavior.
- During a water change, the existing clear-water/pH-normal stop behavior remains immediate; handing over to a different quality problem requires that other condition to be confirmed.
- Readings and warning messages remain live. Confirmation delays automatic actions, not the display of abnormal measurements. The dashboard and desktop controls show pending quality confirmation; fan status shows when temperature is being checked.

## Hardware verification

With the tank attended, confirm Fan On, Fan Off and Auto; check relay polarity and actual fan operation. Verify a brief abnormal reading does not start an automatic action, a sustained condition does, and float switches still stop/correct pumps promptly. Confirm manual fan operation with temperature disabled, then return to Auto and re-enable the sensor. Recheck pH in all three buffers using the unchanged input Nano and its normal power arrangement.
