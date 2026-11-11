# Scheduled water changes

Open **Water-change schedule** on the Pi, or **Water Changes** on the web. Sign in on the web to edit schedules. Choose a day, a time and **Shared duration (seconds)**, from 1 to 600 seconds. Enter **60** for one minute: both pumps start together and share the same end time. The running status shows the approximate time remaining. It refreshes periodically without changing the timer.

At the scheduled time the Pi checks for a connected controller, automatic water mode, fresh Normal float readings, idle pumps and feeder, and calibration off. If ready, one command starts both pumps. The Pi measures the shared duration from dispatch, then requests a stop for both pumps. A 60-second setting is one approximately 60-second scheduled operation. Level corrections count within that time; they do not reset or extend it.

The Pi records completion after the Mega acknowledges the start, any pump adjustments, and the final stop in order. Automatic water control then resumes and may operate a pump if the level still needs correction. Busy or blocked scheduled occurrences are skipped. Scheduled feeds wait while an exchange is active.

A valid change in water level does not cancel an active exchange. High water pauses refill and keeps draining; Low or Very Low water pauses draining and keeps refilling. After the Normal level stays stable for two seconds, both pumps resume. If the opposite limit is reached during recovery, the corrective direction changes immediately without waiting for the two seconds. Commands are sent only when the required pump operation changes, avoiding repeated commands on every reading.

Stale, inconsistent, unavailable or disabled float readings, calibration, or missing pump acknowledgements interrupt the exchange and request a stop. **Stop water change**, **Stop all pumps**, or an individual pump-stop button stops the whole scheduled exchange. Faults and operator stops leave automatic control paused. The Pi withholds its heartbeat while waiting for stop confirmation, allowing the Mega's four-second watchdog to stop the outputs if communication fails. USB loss or restart marks the job interrupted and prevents an automatic replay.

Previously saved schedules keep their days, times and enabled settings. The shorter of their old drain duration and refill timeout becomes the shared runtime. Review the displayed duration before the next scheduled run. Newly saved schedules use the single duration field.

The Pi handles the timer and level corrections through the existing combined Mega command interface. The Normal-level reading remains a start condition; once the exchange is active, valid level changes trigger correction rather than cancellation. The shared timer controls the planned end. Completion means the timed operation finished, not that a measured volume was exchanged.

## What the level protection does

For this scheduled run, the Pi selects both pumps, drain only or refill only using the float readings. The existing Mega manual-pump mode does not independently apply its automatic level-correction rules. The Pi checks and the existing Mega heartbeat watchdog provide the scheduled-run protection; test the actual float switches, corrective direction and pump stops before leaving a schedule unattended.

Equal running time does not mean equal volume: if refill is faster, the tank level rises; if drain is faster, it falls. Float switches measure tank level, so they cannot confirm an empty refill container, a blocked hose, or a relay that fails to turn off. Pumps stopping also cannot stop water that continues to siphon through a hose. Use a short supervised run to check flow balance and hose placement. Some newly added water may be drained during simultaneous operation, so runtime alone does not specify a precise percentage of water replaced.
