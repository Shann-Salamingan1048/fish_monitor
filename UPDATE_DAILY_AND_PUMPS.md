# Every-day feeding and simultaneous pumps — 21 October 2026

This update applies to the desktop GUI and the shared localhost/LAN/Cloudflare web interface.

## What changed

- Custom Alert settings no longer show turbidity warning/critical inputs. Turbidity still uses the input Nano's classifications: Cloudy and Very Cloudy start automatic correction after five continuous seconds. Slightly Cloudy alone does not start it; an active correction continues through Slightly Cloudy until Clear or Very Clear. The input Nano's classification code is unchanged.
- Feeding Schedule now offers **Every day (Monday–Sunday)**. One row repeats at the chosen time and cycle count on all seven days. Existing single-day schedules remain available. Daily rows support editing, pause/resume and deletion just like weekly rows. Times use the configured timezone, normally Asia/Manila. Existing separate rows remain separate schedules.
- Confirmed unsafe pH or Cloudy/Very Cloudy runs **both extraction and insertion pumps**. Five-second confirmation and disabled-sensor exclusions remain in effect.
- **Both pumps — drain + refill** is available under web **Access Mode → Pump controls** and desktop **Controls & settings → Water pumps**.

## Automatic pump priority

| Condition during water-quality correction | Extraction | Insertion |
|---|---|---|
| Normal level; confirmed unsafe pH or cloudy water | On | On |
| Overflow | On | Off |
| Low or Very Low | Off | On |
| Back at Normal; quality still needs correction | On | On |
| Normal level and quality restored | Off | Off |

Enabled float switches take priority immediately, without the five-second quality delay. A level correction continues until Normal. pH and turbidity remain active during a quality correction, including its level overrides. If quality recovers while the level is still abnormal, level correction finishes first. Temperature remains paused while pumping, and feeding waits until the pumps stop.

Disabled sensors do not trigger their associated rules. Disabled float switches do not imply a low or normal level. With Normal disabled, the previous enabled-Upper fallback remains available. With all floats disabled, quality correction can run both pumps but those level protections are bypassed.

Manual drain, refill and both-pump commands preserve the existing manual override behavior: they bypass automatic sensor decisions and run until stopped. Selecting Drain or Refill runs only that pump. Stop All Pumps pauses automation; Resume automatic control restores it. The new automatic level priority described above applies in Auto mode.

## Install over the previous fan-control update

1. Stop the application and pumps. Back up the project and its existing `fish_tank_state.db`.
2. Replace these files, preserving their paths:
   - `config.py`
   - `feeding_scheduler.py`
   - `water_control.py`
   - `local_controls.py`
   - `tkinter_gui.py`
   - `static/script.js`
   - `templates/index.html`
   - `templates/graphs.html`
   - `templates/camera.html`
3. Compile and upload **`arduino/NANO_OUTPUT/NANO_OUTPUT.ino` to the output Nano**. Its updated pump handler recognizes the new `BOTH_ON` command. The previous firmware cannot run both pumps together.
4. **Do not upload or replace your working input-Nano sketch.** Its Pi-connected pH calibration must be preserved. The archive's input sketch is unchanged from the previous release and does not contain the newer values you calibrated yourself.
5. Keep your database; startup extends its schedule-day constraint to support daily recurrence while preserving IDs, cycle counts and history. Start the application normally (`python main.py`) and refresh the browser. JavaScript URLs have a new cache version.

If updating from an older release, use the complete Python/static/templates files from this archive while preserving the database and working input firmware. The new output firmware includes the earlier fan controls.

## Verification

See VALIDATION.md for the software checks. No physical pump, fan, probe, Pi or Nano was available here. Before returning to normal aquarium operation, verify both pumps, overflow → extraction only, Low/Very Low → insertion only, return to Normal, Stop All and Resume Auto on your actual setup. Both-pump output is a command state; the application does not measure actual water flow.
