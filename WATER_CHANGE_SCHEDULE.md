# Scheduled water changes

## Install this update

1. Stop AquaPi. Back up the project folder and keep your existing SQLite database.
2. Update the application files from this package.
3. Upload `arduino/AquaPi_Mega/AquaPi_Mega.ino` to your **Mega 2560** once. Wiring and baud rate are unchanged. Close Serial Monitor afterward.
4. Restart `main.py`. Existing schedules and sensor calibration are retained. New water schedules are stored in their own tables.

Water schedules require this firmware's `WATERCHANGE:V1` support. Older firmware can still use existing controls, but scheduled water changes are skipped with a firmware message.

## Where to configure

- **Web:** Water Changes → Water-change schedule. Sign in as Admin to add, edit, pause, enable or delete schedules.
- **GUI:** Controls & settings → Water pumps → Water-change schedule.

Both interfaces edit the same saved schedules. Select Monday–Sunday or **Every day**, a time, a drain duration (1–600 seconds), and a refill timeout (1–1800 seconds). The displayed timezone is the existing scheduling timezone, normally `Asia/Manila`. Choose durations after measuring/testing pump flow with the installed tubing; seconds do not specify a volume or a percentage.

This is routine water-change scheduling, **not ammonia detection**. AquaPi has no ammonia sensor. It cannot verify ammonia removal, replacement-water quality or the remaining supply of refill water. Prepare suitable replacement water and test your chosen durations under supervision before enabling recurring runs.

## Sequence and protections

1. A run starts only with Normal water level, all three float switches enabled and valid, recent sensor data, automatic water control enabled, no ongoing pumping or feeding, and calibration mode off.
2. The extraction pump drains alone until the selected drain time elapses or the Low float no longer detects water, whichever occurs first.
3. Extraction stops; insertion refills until Normal has remained detected for 3 seconds. The refill timeout includes this allowance.
4. Both pumps stop. After successful completion, automatic control resumes and the existing pH settling period applies.

The Mega runs this sequence locally without blocking sensor/command service. The Upper float, contradictory/missing/disabled float readings, a refill timeout, changed sensor settings or changed calibration/configuration stop the scheduled operation. An Upper-float event cancels the schedule and transfers control to automatic overflow draining. Other faults or a manual stop leave automatic water control paused; inspect the tank and choose **Resume automatic control** when appropriate. System Off and heartbeat expiry also stop the pumps.

Use **Stop water change** in either interface to cancel the current run and pause automatic water control. Pausing/deleting a schedule affects future occurrences; use Stop to cancel an active run. Other manual pump commands override the scheduled sequence and then follow their normal manual behavior.

The schedule worker checks once per second. An occurrence can start within its scheduled minute, at most once per date for that schedule. Busy/unsafe occurrences are skipped rather than queued. Runs missed while the application was off are not caught up. At the same time, overlapping schedules start at most one water change. Feeding waits while any pump or water-change job is active, including the 3-second allowance. Pending feeds no longer expire after 30 minutes; one serving is dispatched at a time.

USB interruption, application restart or uncertain serial delivery does not replay a claimed run. The Mega retains its existing four-second heartbeat timeout. Reconnection keeps automatic pumping paused until an operator resumes it. `main.py` must remain running for schedules; internet/Cloudflare access is not needed for local scheduling.

## Status and verification

Both interfaces show the next occurrence, current drain/refill phase and last result. A serial write is recorded as sent, not completed. Completion requires the Mega's matching job report. Calibration references, NTU/ADC range choice and 100-sample turbidity acquisition are unchanged.

See `VALIDATION.md` for automated checks. Physical pump flow, water-level placement, refill supply and electrical interference still require testing on your aquarium.
