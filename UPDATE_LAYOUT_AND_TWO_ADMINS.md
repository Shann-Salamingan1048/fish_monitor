# AquaPi dashboard order, two Admin sessions, and individual pump stops

The web home page now reads top to bottom on desktop and mobile:

1. System Status
2. Feeding Schedule
3. Quick Actions
4. Access Mode
5. Feed Your Fish Now
6. Sensor Activation

Feed Your Fish Now retains custom cycles and Feed now. Stop feeder, Pause automatic feeding and Resume automatic feeding were removed from the web panel. Desktop feeder controls and the server's shutdown/cancellation behavior remain available. If automatic feeding was previously paused, resume it in the desktop Feeding tab.

## Two signed-in devices

The existing Admin PIN is retained. At most two browser sessions can hold Admin access simultaneously. A third correct-PIN login receives an explanatory message and remains a viewer. This limit is separate from Maximum Website Viewers.

Tabs sharing browser cookies use one slot. Separate browsers/profiles count separately, even on the same physical device. The desktop GUI is a trusted local interface and does not occupy a web Admin slot.

Logout frees the slot immediately and invalidates the same session in other tabs. Slots with no server contact expire after five minutes. Pages send an authentication heartbeat every 30 seconds; suspended devices can time out. Closing a tab does not immediately log out all other tabs. After expiry or app restart, sign in again. Expired or logged-out cookies cannot restore Admin privileges by themselves.

The session registry belongs to AquaPi's existing single main.py process. Do not run independent web workers, multiple app instances, or simultaneous laptop/Pi origins for this limit; those do not share this registry.

## GUI pump controls

Open Controls & settings -> Water pumps. Stop draining stops extraction only. Stop refilling stops insertion only. If both are running, the other pump continues. Both actions enter manual control so automation cannot immediately restart the stopped pump. Stop pumps / pause auto stops both. Resume automatic water control restores automation.

## Installation

Stop main.py, back up the project/database, and replace the project files while preserving your database and local environment settings. Include the new admin_sessions.py file. Restart once and refresh the browser (static asset version 16). No Nano firmware upload is needed specifically for this update. The supplied input sketch and serial-only turbidity ADC are preserved, as are camera-page custom feeding cycles.

## Validation

134 Python tests passed, including concurrent login admission, logout/reused-cookie rejection, session expiry, and selective pump stops. DOM checks passed on the dashboard, camera and graph pages, including section order and simplified feeder controls. Hardware was not available for testing.
