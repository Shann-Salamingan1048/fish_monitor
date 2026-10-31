# mcu_comm.py
"""
Microcontroller Communication Module.

Handles:
- Input Nano sensor data
- Output Nano camera commands
- feeder commands
- cooler temperature forwarding
- Output Nano identification/status
"""

import serial
import time
import threading
from typing import Optional, List, Callable

import config
import fan_control
import calibration

from config import (
    PORT_1,
    PORT_2,
    BAUD_RATE,
    sensor_data,
    sensor_history,
    serial_ports,
    output_serial_ports,
    get_servo_position,
    set_db_value,
    send_to_output_serial_ports,
    build_camera_serial_message,
    build_cooler_temperature_message,
    register_output_serial_port,
    unregister_output_serial_port,
    register_input_serial_port,
    unregister_input_serial_port,
)

from predictions import (
    build_sensor_alerts,
    format_float_status,
    record_sensor_history_locked,
)
import water_control
import sensor_control
import feeding_control


class MCUCommunicator:
    """Handles serial communication with the input and output Nano boards."""

    def __init__(self, app_callback: Optional[Callable] = None):
        self.app_callback = app_callback
        self.serial_connections: List[serial.Serial] = []
        self.running = False
        self._lock = threading.Lock()
        self._off_ack = threading.Event()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect_to_ports(
        self,
        port1: str = PORT_1,
        port2: str = PORT_2,
    ) -> List[serial.Serial]:
        connections = []

        for port in (port1, port2):
            try:
                ser = serial.Serial(
                    port,
                    BAUD_RATE,
                    timeout=1,
                    write_timeout=1,
                )

                connections.append(ser)
                print(f"[SERIAL] Connected to {port}")

            except serial.SerialException as exc:
                print(
                    f"[SERIAL] Could not open {port}: {exc}"
                )

        with self._lock:
            self.serial_connections = connections

            serial_ports.clear()
            serial_ports.extend(connections)

        return connections

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
            "NANOOUTPUT,"
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
    # Output Nano status
    # ------------------------------------------------------------------

    def _parse_output_message(self, line):
        if "SYSTEM:OFF" in line:
            self._off_ack.set()
        if 'REASON:HOST_TIMEOUT' in line and not config.shutting_down:
            print('[SYSTEM] Output Nano heartbeat timed out; system stopped. Restart the application to resume.')
            config.begin_shutdown()
            water_control.stop_all_for_shutdown()
            feeding_control.disconnect()
        fan_control.handle_status(line)
        feeding_control.handle_status(line)
        sensor_control.output_status(line)
        if "COOLER:ON" in line:
            print("[COOLER] Output Nano reports cooler ON")
        elif "COOLER:OFF" in line:
            print("[COOLER] Output Nano reports cooler OFF")

        if "PUMP_EXTRACT:ON" in line:
            print("[WATER] Output Nano reports extraction pump ON")
        if "PUMP_EXTRACT:OFF" in line:
            print("[WATER] Output Nano reports extraction pump OFF")
        if "PUMP_INSERT:ON" in line:
            print("[WATER] Output Nano reports insertion pump ON")
        if "PUMP_INSERT:OFF" in line:
            print("[WATER] Output Nano reports insertion pump OFF")

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
    # Input Nano sensor packet
    # ------------------------------------------------------------------

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
                                         None if calibration.active() else sensor_data['ph'])
            snapshot = sensor_data.copy()
            history = {key: list(values) for key, values in sensor_history.items()}
            alerts = build_sensor_alerts(snapshot, history)
            if water_control.get_sensor_policy()['temp']:
                try:
                    numeric_temp = float(snapshot['temp'])
                except (TypeError, ValueError):
                    numeric_temp = None
                if numeric_temp is not None and -55.0 <= numeric_temp <= 125.0:
                    send_to_output_serial_ports(build_cooler_temperature_message(numeric_temp, config.TEMP_OVERHEAT_LIMIT_C))
                else:
                    send_to_output_serial_ports('NANOOUTPUT,TEMP:INVALID\n')
            elif sensor_control.enabled('temp'):
                # Paused packets must not count toward a pending fan change.
                send_to_output_serial_ports('NANOOUTPUT,TEMP:INVALID\n')
        if self.app_callback:
            try:
                self.app_callback(snapshot['temp'], snapshot['float_stat'], snapshot['turbidity'], snapshot['ph'], alerts)
            except Exception as exc:
                print('[GUI] Callback error:', exc)
        return snapshot, alerts

    # ------------------------------------------------------------------
    # Serial reader
    # ------------------------------------------------------------------

    def read_serial_data(self, port):
        ser = None

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

            # Nano normally resets when the serial connection opens.
            time.sleep(2)

            last_heartbeat = 0.0

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

                            # Identify which Nano owns this serial port.
                            if line.startswith(("NANOOUTPUT", "NANOSERVO")):
                                first_output_identification = ser not in config.output_serial_ports
                                register_output_serial_port(ser)
                                if first_output_identification or 'READY:1' in line:
                                    self._send_serial_message(ser, 'NANOOUTPUT,SYSTEM:ON\n')
                                    self.send_servo_restore_command(ser)
                                    sensor_control.sync_output()
                                    water_control.restore_pump_state_to_hardware()

                            if line.startswith(("NANOINPUT,", "NANOINPUTSTATUS")):
                                first_input_identification = ser not in config.input_serial_ports
                                register_input_serial_port(ser)
                                if first_input_identification or 'READY:1' in line:
                                    self._send_serial_message(ser, 'NANOINPUT,SYSTEM:ON\n')
                                    sensor_control.input_reconnected()
                                    water_control.restore_sensor_mode_to_hardware()

                            if line.startswith("NANOOUTPUT"):
                                self._parse_output_message(line)

                            if line.startswith("NANOSERVO"):
                                self._parse_servo_message(line)

                            if line.startswith("NANOINPUT,"):
                                self._parse_sensor_message(line)

                except serial.SerialException:
                    raise

                except Exception as exc:
                    print(
                        f"[SERIAL] Parse error "
                        f"on {port}: {exc}"
                    )

                now = time.monotonic()
                if now - last_heartbeat >= 1.0:
                    if ser in config.output_serial_ports:
                        self._send_serial_message(ser, 'NANOOUTPUT,HEARTBEAT:1\n')
                    elif ser in config.input_serial_ports:
                        self._send_serial_message(ser, 'NANOINPUT,HEARTBEAT:1\n')
                    last_heartbeat = now
                sensor_control.retry_pending_sync()
                fan_control.retry_sync()
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
                with self._lock:
                    if ser in self.serial_connections:
                        self.serial_connections.remove(ser)

                    if ser in serial_ports:
                        serial_ports.remove(ser)

                if ser in config.output_serial_ports:
                    feeding_control.disconnect()
                    fan_control.disconnected()
                unregister_output_serial_port(ser)
                unregister_input_serial_port(ser)

                try:
                    if ser.is_open:
                        ser.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start_reading(
        self,
        port1=PORT_1,
        port2=PORT_2,
    ):
        self.running = True

        for port in (port1, port2):
            thread = threading.Thread(
                target=self._read_with_reconnect,
                args=(port,),
                daemon=True,
            )
            thread.start()

    def _read_with_reconnect(self, port):
        while self.running:
            self.read_serial_data(port)
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
            sensor_snapshot = sensor_data.copy()

            history_snapshot = {
                key: list(values)
                for key, values in sensor_history.items()
            }

        return build_sensor_alerts(
            sensor_snapshot,
            history_snapshot,
        )


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
