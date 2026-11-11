# mcu_comm.py
"""
Microcontroller Communication Module.

Handles:
- MegaInput sensor data
- MegaOutput camera commands
- feeder commands
- Mega-local control status
- MegaOutput identification/status
"""

import math
import serial
import time
import threading
from typing import Optional, List, Callable

import config
import fan_control
import calibration
import mega_control
from serial_discovery import MegaPortDiscovery

from config import (
    MEGA_PORT,
    BAUD_RATE,
    sensor_data,
    sensor_history,
    serial_ports,
    output_serial_ports,
    get_servo_position,
    set_db_value,
    send_to_output_serial_ports,
    build_camera_serial_message,
    unregister_output_serial_port,
    unregister_input_serial_port,
)

from predictions import (
    build_sensor_alerts,
    format_float_status,
    record_sensor_history_locked,
)
import water_control
import water_schedule
import sensor_control
import feeding_control


class MCUCommunicator:
    """Handles one Mega USB connection for MegaInput and MegaOutput."""

    def __init__(self, app_callback: Optional[Callable] = None):
        self.app_callback = app_callback
        self.serial_connections: List[serial.Serial] = []
        self.running = False
        self._lock = threading.Lock()
        self._off_ack = threading.Event()
        self._discovery = MegaPortDiscovery()
        self._seen_roles = set()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def disconnect_all(self):
        with self._lock:
            for ser in self.serial_connections:
                try:
                    if ser and ser.is_open:
                        ser.close()
                except Exception as exc:
                    print(
                        "[SERIAL] Close error:",
                        exc,
                    )

            self.serial_connections.clear()
            serial_ports.clear()
            output_serial_ports.clear()
            config.input_serial_ports.clear()

    # ------------------------------------------------------------------
    # Serial send
    # ------------------------------------------------------------------

    def _send_serial_message(
        self,
        ser,
        message,
    ):
        return config._send_to_serial_targets([ser], message)

    def send_servo_restore_command(self, ser):
        position = get_servo_position()

        message = (
            "MegaOutput,"
            "CAMERA:SET,"
            f"TILT:{position['tilt']},"
            f"PAN:{position['pan']},"
            "FEED:NO\n"
        )

        return self._send_serial_message(
            ser,
            message,
        )

    def send_command(self, command):
        if command == "feed":
            return feeding_control.request_feed(1)
        message = build_camera_serial_message(
            command
        )

        return send_to_output_serial_ports(
            message
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_value(part):
        if ":" not in part:
            return ""

        return (
            part
            .split(":", 1)[1]
            .strip()
        )

    # ------------------------------------------------------------------
    # MegaOutput status
    # ------------------------------------------------------------------

    def _parse_output_message(self, line):
        if "SYSTEM:OFF" in line:
            self._off_ack.set()
        if 'REASON:HOST_TIMEOUT' in line and not config.shutting_down:
            print('[SERIAL] Output heartbeat expired; stopping pumps and reconnecting.')
            self._pause_after_connection_loss()
            raise serial.SerialException('MegaOutput HOST_TIMEOUT')
        fan_control.handle_status(line)
        feeding_control.handle_status(line)
        sensor_control.output_status(line)
        if "COOLER:ON" in line:
            print("[COOLER] MegaOutput reports cooler ON")
        elif "COOLER:OFF" in line:
            print("[COOLER] MegaOutput reports cooler OFF")

        if 'PUMP_EXTRACT:' in line and 'PUMP_INSERT:' in line:
            fields = dict(part.split(':', 1) for part in line.split(',') if ':' in part)
            water_control.accept_pump_status(fields)
            water_schedule.pump_feedback(fields)
        if "PUMP_EXTRACT:ON" in line:
            print("[WATER] MegaOutput reports extraction pump ON")
        if "PUMP_EXTRACT:OFF" in line:
            print("[WATER] MegaOutput reports extraction pump OFF")
        if "PUMP_INSERT:ON" in line:
            print("[WATER] MegaOutput reports insertion pump ON")
        if "PUMP_INSERT:OFF" in line:
            print("[WATER] MegaOutput reports insertion pump OFF")

    # ------------------------------------------------------------------
    # Servo status
    # ------------------------------------------------------------------

    def _parse_servo_message(self, line):
        parts = line.split(",")

        for part in parts:
            part = part.strip()

            if part.startswith("TILT:"):
                set_db_value(
                    "servo_tilt_angle",
                    self._extract_value(part),
                )

            elif part.startswith("PAN:"):
                set_db_value(
                    "servo_pan_angle",
                    self._extract_value(part),
                )

    # ------------------------------------------------------------------
    # MegaInput sensor packet
    # ------------------------------------------------------------------

    def simulate_readings(self, stop_event):
        """Send example Mega packets through the regular desktop and web data path."""
        started = time.monotonic()
        while not stop_event.is_set():
            phase = (time.monotonic() - started) / 10
            temp = 27 + 0.7 * math.sin(phase)
            ph = 7.2 + 0.12 * math.sin(phase / 2)
            adc = round(850 + 120 * math.sin(phase))
            clarity = next(row['label'] for row in calibration.DEFAULT_RANGES
                           if row['min'] <= adc <= row['max'])
            revision = sensor_control.get_status()['revision']
            self._parse_sensor_message(
                f'MegaInput,Temp:{temp:.1f},pH:{ph:.2f},pHADC:{round(885 - (ph - 7.2) * 50)},'
                f'Turbidity:{clarity},TurbidityADC:{adc},FloatLow:1,FloatNormal:1,FloatUpper:0,'
                f'SENSORCFG:{revision}')
            stop_event.wait(1)

    def _parse_sensor_message(self, line):
        # Serialize packet decisions and operator overrides: a concurrent old
        # packet must never restart a just-disabled sensor's action.
        with config.sensor_lock:
            if config.shutting_down:
                return sensor_data.copy(), []
            fields = dict(part.strip().split(':', 1) for part in line.split(',') if ':' in part)
            if 'PH' in fields or 'Ph' in fields:
                fields['pH'] = fields.get('PH', fields.get('Ph'))
            if not sensor_control.accept_packet(fields):
                return sensor_data.copy(), build_sensor_alerts(sensor_data, sensor_history)
            config.last_sensor_received_at = time.time()
            if any(key in fields for key in ('FloatLow', 'FloatNormal', 'FloatUpper')):
                float_val = sensor_control.water_level()
            elif sensor_control.all_floats_enabled():
                # Compatibility for the old input sketch while all sensors are on.
                float_val = format_float_status(fields.get('FloatSensor', fields.get('FloatSensor1', '--')))
            else:
                float_val = sensor_control.water_level()
            water_control.process_sensor_update(float_val, fields.get('Turbidity'), fields.get('pH'))
            sensor_control.refresh_display()
            sensor_data['float_stat'] = float_val
            record_sensor_history_locked(sensor_data['temp'],
                                         None if calibration.active() else sensor_data['turbidity'],
                                         None if calibration.active() else sensor_data['ph'],
                                         turbidity_adc=None if calibration.active() else sensor_data['turbidity_adc'])
            snapshot = sensor_data.copy()
            alerts = build_sensor_alerts(snapshot, sensor_history)
        if self.app_callback:
            try:
                self.app_callback(snapshot['temp'], snapshot['float_stat'], snapshot['turbidity'], snapshot['ph'], alerts)
            except Exception as exc:
                print('[GUI] Callback error:', exc)
        return snapshot, alerts

    # ------------------------------------------------------------------
    # Serial reader
    # ------------------------------------------------------------------

    def _pause_after_connection_loss(self):
        if config.shutting_down:
            return
        water_control.pause_for_connection_loss()
        # Stop any feed still running on a surviving output connection. Never
        # replay an uncertain job when the board returns.
        feeding_control.disconnect()
        water_schedule.disconnect()
        config.send_to_identified_output_serial_ports('MegaOutput,FEED:STOP,JOB:0\n')
        fan_control.stop_for_shutdown()

    def _identify_mega(self, ser, reset=False):
        first = ser not in config.output_serial_ports or ser not in config.input_serial_ports
        if not first and not reset:
            return False
        if any(p is not ser for p in config.output_serial_ports + config.input_serial_ports):
            raise serial.SerialException('Another controller is already connected')
        recovered = 'mega' in self._seen_roles
        self._seen_roles.add('mega')
        if recovered:
            self._pause_after_connection_loss()
        unregister_output_serial_port(ser)
        unregister_input_serial_port(ser)
        commands = ['MegaOutput,SYSTEM:OFF\n'] if recovered else []
        commands += ['MegaOutput,SYSTEM:ON\n', 'MegaOutput,PUMP:STOP_ALL\n']
        if recovered:
            commands.append('MegaOutput,FEED:STOP,JOB:0\n')
        for command in commands:
            if not self._send_serial_message(ser, command):
                raise serial.SerialException('Could not initialize Mega')
        # One physical handle serves both existing logical APIs. One reader only.
        config.register_mega_serial_port(ser)
        sensor_control.sync_output()
        sensor_control.input_reconnected()
        mega_control.reset()
        mega_control.sync(force=True)
        mode = 'MANUAL' if water_control.get_status()['manualOverride'] else 'AUTO'
        self._send_serial_message(ser, f'MegaOutput,WATERMODE:{mode}\n')
        if not recovered:
            self.send_servo_restore_command(ser)
        self._mega_armed_at = time.monotonic()
        print('[SERIAL] Mega ' + ('reconnected; pumps remain stopped.' if recovered else 'identified (input + output).'))
        return True

    def read_serial_data(self, port):
        ser = None
        role = None

        try:
            ser = serial.Serial(
                port,
                BAUD_RATE,
                timeout=1,
                write_timeout=1,
            )

            with self._lock:
                self.serial_connections.append(ser)

                if ser not in serial_ports:
                    serial_ports.append(ser)

            print(
                f"[SERIAL] Connected to {port}"
            )

            # Mega normally resets when the serial connection opens.
            time.sleep(2)

            last_heartbeat = 0.0
            last_received = time.monotonic()
            role = None

            while self.running:
                try:
                    if ser.in_waiting > 0:
                        line = (
                            ser.readline()
                            .decode(
                                "utf-8",
                                errors="replace",
                            )
                            .strip()
                        )

                        if line:
                            print(
                                f"[{port}] {line}"
                            )

                            identity = line.startswith("AquaPiMega,") and ',PROTOCOL:1,' in line
                            if role == "mega" or identity:
                                last_received = time.monotonic()
                            if identity:
                                role = 'mega'
                                initialized = self._identify_mega(ser, 'READY:1' in line)
                                if initialized:
                                    self._parse_output_message(line)
                                elif ('SYSTEM:OFF' in line and not config.shutting_down
                                      and time.monotonic() - self._mega_armed_at > 2):
                                    raise serial.SerialException('Mega stopped; reconnecting safely')
                            elif role != 'mega':
                                if time.monotonic() - last_received > 8.0:
                                    raise serial.SerialException('Expected current AquaPi Mega firmware')
                                continue
                            if line.startswith('AquaPiMega,'):
                                mega_control.handle_status(line)
                            if line.startswith("MegaOutput"):
                                self._parse_output_message(line)

                            if line.startswith("MegaServo"):
                                self._parse_servo_message(line)

                            if line.startswith("MegaInput,"):
                                self._parse_sensor_message(line)

                except serial.SerialException:
                    raise

                except Exception as exc:
                    print(
                        f"[SERIAL] Parse error "
                        f"on {port}: {exc}"
                    )

                now = time.monotonic()
                allow_heartbeat = role != 'mega' or water_schedule.service_exchange()
                if allow_heartbeat and now - last_heartbeat >= 1.0:
                    if ser in config.output_serial_ports:
                        self._send_serial_message(ser, 'MegaOutput,HEARTBEAT:1\n')
                    last_heartbeat = now
                if not config.shutting_down and now - last_received > 8.0:
                    raise serial.SerialException('No Mega messages for 8 seconds')
                sensor_control.retry_pending_sync()
                fan_control.retry_sync()
                mega_control.sync()
                time.sleep(0.02)

        except serial.SerialException as exc:
            print(
                f"[SERIAL] Error on {port}: {exc}"
            )

        except Exception as exc:
            print(
                f"[SERIAL] Unexpected error "
                f"on {port}: {exc}"
            )

        finally:
            if ser is not None:
                identified = ser in config.output_serial_ports or ser in config.input_serial_ports
                unregister_output_serial_port(ser)
                unregister_input_serial_port(ser)
                with self._lock:
                    if ser in self.serial_connections:
                        self.serial_connections.remove(ser)
                    if ser in serial_ports:
                        serial_ports.remove(ser)
                # Always release the old descriptor before DB/UI recovery work.
                try:
                    ser.close()
                except Exception:
                    pass
                if identified and self.running and not config.shutting_down:
                    self._pause_after_connection_loss()
                if role == 'mega':
                    feeding_control.disconnect()
                    water_schedule.disconnect()
                    fan_control.disconnected()

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start_reading(
        self,
        port=MEGA_PORT,
    ):
        if self.running:
            return
        self.running = True

        ports = [port]
        for port in ports:
            thread = threading.Thread(
                target=self._read_with_reconnect,
                args=(port,),
                daemon=True,
            )
            thread.start()

    def _read_with_reconnect(self, port):
        while self.running:
            device = self._discovery.acquire(port)
            if device is not None:
                try:
                    if device != port:
                        print(f'[SERIAL] Rediscovered {port} at {device}')
                    self.read_serial_data(device)
                except Exception as exc:
                    print(f'[SERIAL] Recovery error on {device}: {exc}; retrying.')
                finally:
                    self._discovery.release(port)
            if self.running:
                time.sleep(2)

    def stop_reading(self):
        self._off_ack.clear()
        config.begin_shutdown()
        if self.running and config.output_serial_ports:
            self._off_ack.wait(timeout=0.75)
        self.running = False
        self.disconnect_all()

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def get_sensor_data(self):
        with config.sensor_lock:
            return sensor_data.copy()

    def get_sensor_history(self):
        with config.sensor_lock:
            return {
                key: list(values)
                for key, values in sensor_history.items()
            }

    def get_alerts(self):
        with config.sensor_lock:
            return build_sensor_alerts(sensor_data, sensor_history)



_default_communicator = None


def get_communicator(app_callback=None):
    global _default_communicator

    if _default_communicator is None:
        _default_communicator = MCUCommunicator(
            app_callback
        )

    elif (
        app_callback is not None
        and _default_communicator.app_callback is None
    ):
        _default_communicator.app_callback = app_callback

    return _default_communicator


def read_serial_data(app, port):
    callback = (
        app.update_labels
        if hasattr(app, "update_labels")
        else None
    )

    communicator = get_communicator(
        callback
    )

    communicator.read_serial_data(port)


def send_command(command):
    communicator = get_communicator()
    return communicator.send_command(command)
