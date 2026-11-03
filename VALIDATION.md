# Water-change scheduling release validation

## Current update

- 223 Python tests passed (including schedule CRUD, weekday/everyday selection, atomic claims, start guards, missed-run handling, no replay, feed conflicts, deadline stopping, access control, cross-site write rejection, and GUI use of the shared service).
- Water schedule JavaScript syntax and jsdom interaction checks passed: viewer/Admin controls, adding/editing, draft preservation during polling, pause/enable, deletion and Stop.
- Four C++ simulations passed using the actual combined `.ino`. The new simulation covers drain timing and Low cutoff, refill to Normal, refill timeout, Upper/contradictory floats, disabled sensors, manual cancellation, System Off, heartbeat expiry, duplicate jobs, feeding/calibration conflicts, timer rollover and serial backpressure.
- Actual Mega AVR compilation passed. Final build: 33,002 bytes flash (12%), 3,780 bytes static RAM (46%), 4,412 bytes remaining for stack/heap. This is not a measured worst-case stack watermark.
- Existing NTU/ADC calibration, manual pump controls, feeds, sensor controls and reconnect regression tests passed.

The GUI editor's save path was tested against the shared schedule service. A full Tk display check was unavailable in this environment. No real pumps, float switches, refill supply or USB electrical-noise test was available; automated checks do not establish physical water volume or ammonia concentration.

## Repeat checks

```bash
python -m pytest -q
node --check static/water_schedule.js
python tests/render_pages.py
# Development-only Node dependency: npm install --no-save jsdom
node tests/frontend_water_schedule_test.cjs
arduino-cli compile --fqbn arduino:avr:mega:cpu=atmega2560 arduino/AquaPi_Mega
g++ -std=c++17 -I tests/firmware_stubs tests/mega_water_schedule_test.cpp -o /tmp/aquapi-water-test
/tmp/aquapi-water-test
```

Also run the existing `mega_firmware_test.cpp`, `mega_policy_test.cpp` and `mega_turbidity_test.cpp` in the same way. Arduino toolchain: AVR Boards 1.8.8, OneWire 2.3.8, DallasTemperature 4.0.6, Adafruit PWM Servo Driver Library 3.0.3 and Adafruit BusIO 1.17.4.

