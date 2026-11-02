# Mega-only release validation

## Scope

One Mega controller, one USB reader and one complete Arduino sketch. The serial protocol consistently uses `MegaInput`, `MegaInputStatus`, `MegaOutput`, `MegaServo`, `MegaConfig` and `AquaPiMega`. The old alternative controller mode and separate sketch targets have been removed.

The original Mega-only migration requires firmware and application to be updated together. This turbidity update changes only firmware, tests and documentation; an existing installation of this Mega-only Python application does not need Python changes. Hardware pin assignments, calibration storage, local automatic control, manual controls and communication-loss stopping remain as described in `MEGA_SETUP.md`.

## Results

- **173 Python tests passed.** These cover the active Mega connection path, command names, configuration synchronization, serial renumbering, connection-loss handling, manual pumps, feeding and schedules, authentication, calibration storage, sensor configuration, settings, unit conversion, API routes and web rendering.
- **Three C++ firmware simulations passed** using the actual single `.ino` file. They exercise combined sensor/actuator operation, direct local fan control, pump pairing, heartbeat expiry, whole serial frames, backpressure, command overflow, rollover, local quality confirmation, float priority, pH settling, calibration mode and outlier rejection. The added turbidity simulation verifies exactly 100 spaced samples, full-scale accumulator safety, ADC polarity, fractional means, NTU reference validation and interpolation, out-of-range handling, reset during acquisition, commands during sampling, and timer rollover.
- **Actual AVR build passed** for `arduino:avr:mega:cpu=atmega2560`: 31,312 bytes flash (12%); 3,509 bytes static RAM (42%); 4,683 bytes remaining for runtime stack/heap. This is not a measured worst-case stack watermark.
- In the preceding release, JavaScript syntax checks passed and all eight web pages passed DOM checks for navigation, roles, settings, units, sensors, fan/pumps, schedules and custom feeding status. Web files are unchanged in this turbidity update.

Tests that exercised the retired Pi-side automatic pump decision path were migrated to the Mega firmware simulations; the host tests now verify settings synchronization and reported hardware state instead of asserting that the Pi sends automatic pump commands.

Toolchain: Arduino AVR Boards 1.8.8; OneWire 2.3.8; DallasTemperature 4.0.6; Adafruit PWM Servo Driver Library 3.0.3; Adafruit BusIO 1.17.4.

## Repeat checks

From the project folder, with development dependencies installed:

```bash
python -m pytest -q
arduino-cli compile --fqbn arduino:avr:mega:cpu=atmega2560 arduino/AquaPi_Mega
g++ -std=c++17 -I tests/firmware_stubs tests/mega_firmware_test.cpp -o /tmp/aquapi-mega-test
/tmp/aquapi-mega-test
g++ -std=c++17 -I tests/firmware_stubs tests/mega_policy_test.cpp -o /tmp/aquapi-policy-test
/tmp/aquapi-policy-test
g++ -std=c++17 -I tests/firmware_stubs tests/mega_turbidity_test.cpp -o /tmp/aquapi-turbidity-test
/tmp/aquapi-turbidity-test
```

No physical Mega, pump driver, aquarium or Raspberry Pi USB noise test was available here. Confirm wiring and calibration on the actual system before enabling automatic pumping. A successful build does not resolve a physical USB disconnection fault.
