# AquaPi interface update — 30 October 2026

## Install over the working application

1. Stop `main.py` normally.
2. Back up your working `fish_monitor` folder, especially `fish_tank_state.db` (or your `FISHTANK_DB_FILE` location).
3. Copy the contents of this archive's `fish_monitor` folder over the working folder. Include the new `admin_credentials.py`, `gui_snapshot.py` and all files in `templates/` and `static/`.
4. Keep your existing database, environment variables and virtual environment. This archive contains no replacement database. No new runtime dependency is needed.
5. Start `python main.py` from your usual virtual environment. Reload the browser. If it still shows the old page, hard-refresh it.

Both Nano sketches are unchanged from the immediately preceding release. This interface update needs no firmware upload. Keep your working input calibration and firmware. Earlier firmware instructions elsewhere in the archive are historical release notes.

## Separate web pages

The navigation links open Overview, Feeding, Controls, Sensors, Camera, Graphs and Settings as separate URLs. The Admin/Viewer link opens Access. Browser Back and direct links work. The camera stream is opened only on Camera.

- Overview: sensor values, pump/fan status and notifications.
- Feeding: daily/weekly schedule and custom Feed now cycles. No manual Stop feeder or Pause auto feeder buttons on the web.
- Controls: both pumps, individual drain/refill starts and stops, and fan mode.
- Sensors: individual sensor activation.
- Camera: movement and custom feeding cycles.
- Graphs: individual live charts.
- Settings: alert thresholds and expandable advanced settings.

The two signed-in browser-session limit remains. Tabs using the same browser cookies share one session.

## Clearer pH names

There are two severity levels, Warning and Critical. The form groups the values into **pH · Warning range** and **pH · Critical limits**, each with **Lower** and **Upper** fields. There are no separate severity names such as High Critical or Lowest Critical.

Both directions remain protected: a reading at/below the critical lower limit or at/above the critical upper limit is Critical. One numeric threshold cannot represent both acidic and alkaline extremes. Existing values and critical-only automatic action rules are preserved. Notifications explain the direction with “pH is too acidic” or “pH is too alkaline.”

## Change the Admin PIN in the desktop GUI

Open **Controls & settings → Admin PIN**. Enter the current PIN, new PIN and confirmation, then Save PIN. The new PIN needs 4–128 characters and no leading/trailing spaces. PIN hashing runs outside the Tk event loop.

The database stores a password hash, not the new PIN. Changing it signs out both web Admin sessions; sign in again using the new PIN. The saved hash persists after restarting. Before the first GUI change, the current PIN is your `FISHTANK_ADMIN_PIN`, or the existing default if that variable was never set. After a GUI change, the saved database hash takes priority over that variable. Keep the database when moving the app to another machine.

## Feeding status

The old “Waiting for the Nano to confirm completion” text is removed. The page shows Starting, Feeding, Completed, Stopped, Check feeder or Failed. The request message clears when the reported job moves on. A successful send is still not falsely reported as a completed physical feeding. Existing busy/pump interlocks and firmware completion tracking remain.

## GUI notification responsiveness

Sensor/history snapshots and alert calculations now run in a background worker. The Tk event loop consumes only the latest result; rapid incoming packets do not create an ever-growing queue. Unchanged rounded card backgrounds and unchanged notification cards skip redraw work. No Tk widget is accessed by the worker.

These changes remove identified sources of UI blocking and redundant redraws. The actual improvement on the Pi still needs to be observed on your display with live sensors. Modal error/confirmation dialogs remain for failed actions and destructive schedule changes.

## Verification

150 Python tests passed, including PIN persistence/session revocation, invalid PIN changes, background snapshot coalescing, notification redraw caching and all existing control tests. All eight pages passed DOM integration checks. Rendered Chromium checks covered phone (390 px) and desktop (1366 px) widths, no horizontal overflow, page initialization, navigation and browser Back. Python and JavaScript syntax passed.

Browser checks use simulated readings. No physical Raspberry Pi/Nano/camera/pump test or live Tk display test was performed. The calibrated input sketch and output sketch are byte-identical to the previous archive.
