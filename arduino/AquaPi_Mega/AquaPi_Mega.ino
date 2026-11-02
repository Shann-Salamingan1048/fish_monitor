// AquaPi Mega 2560: sensors + actuators, one USB serial connection.
// One complete uploadable sketch. No EEPROM calibration writes.
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <ctype.h>
#include <math.h>
#include <stdlib.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>

namespace AquaPi {
// Complete frames are queued atomically. No Serial.print waits in the main loop.
// On congestion, drop a whole frame and count it; never emit a partial frame.
class SerialFrames : public Print {
  static constexpr uint16_t CAPACITY = 1024;
  uint8_t queue[CAPACITY];
  uint8_t frame[256];
  uint16_t head = 0, tail = 0, used = 0, length = 0;
  bool overflow = false;
public:
  uint32_t dropped = 0;
  using Print::write;
  size_t write(uint8_t value) override {
    if (length < sizeof(frame)) frame[length++] = value;
    else overflow = true;
    if (value == '\n') {
      if (!overflow && length <= CAPACITY - used) {
        for (uint16_t i = 0; i < length; ++i) {
          queue[head] = frame[i]; head = (head + 1) & (CAPACITY - 1);
        }
        used += length;
      } else ++dropped;
      length = 0; overflow = false;
    }
    return 1;
  }
  void drain() {
    int free = Serial.availableForWrite();
    uint8_t budget = 32;
    while (used && free > 0 && budget--) {
      Serial.write(queue[tail]); tail = (tail + 1) & (CAPACITY - 1);
      --used; --free;
    }
  }
};
SerialFrames link;
namespace MegaOutput {

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver();

// The Pi must arm the controller, then send HEARTBEAT:1 every second.
// OFF/timeout latches off until a new SYSTEM:ON (normally app restart).
bool systemEnabled = false;
uint32_t lastHostHeartbeat = 0;
constexpr uint32_t HOST_TIMEOUT_MS = 4000;
bool shutdownServoPending = false;
uint32_t shutdownServoStarted = 0;

// ============================== Hardware ==============================

constexpr uint16_t SERVO_MIN_PULSE = 120;
constexpr uint16_t SERVO_MAX_PULSE = 520;

constexpr uint8_t PAN_CHANNEL = 0;
constexpr uint8_t TILT_CHANNEL = 1;
constexpr uint8_t FEEDER_CHANNEL = 2;

constexpr uint8_t COOLER_RELAY_PIN = 5;
constexpr bool COOLER_RELAY_ACTIVE_LOW = false;

// Motor driver connections requested by the project:
// IN1 -> D22 = extraction pump
// IN3 -> D23 = insertion/fresh-water pump
// IN2 and IN4 must remain at logic LOW for this direction mapping.
constexpr uint8_t EXTRACTION_PUMP_PIN = 22;
constexpr uint8_t INSERTION_PUMP_PIN = 23;
constexpr bool PUMP_ACTIVE_HIGH = true;

// ============================== Camera ==============================

constexpr uint8_t CAMERA_STEP_DEGREES = 10;
constexpr uint8_t TILT_MIN_DEGREES = 30;
constexpr uint8_t TILT_MAX_DEGREES = 130;
constexpr uint8_t PAN_MIN_DEGREES = 40;
constexpr uint8_t PAN_MAX_DEGREES = 180;
constexpr unsigned long CAMERA_COMMAND_INTERVAL_MS = 120;

int tiltAngle = 90;
int panAngle = 110;
unsigned long lastCameraCommandAt = 0;

// ============================== Feeder ==============================

constexpr uint8_t FEEDER_OPEN_DEGREES = 180;
constexpr uint8_t FEEDER_CLOSED_DEGREES = 0;
constexpr uint8_t SCHEDULED_FEED_CYCLES = 3;
constexpr unsigned long FEEDER_POSITION_HOLD_MS = 1000UL;

enum class FeederState : uint8_t {
  IDLE,
  HOLDING_OPEN,
  HOLDING_CLOSED
};

FeederState feederState = FeederState::IDLE;
uint32_t queuedFeedCycles = 0;
uint32_t feederJob = 0;
// No application serving-size cap: the cycle counter uses its full uint32_t range.
void stopFeeder();
unsigned long feederStateStartedAt = 0;

// ============================== Cooler ==============================

constexpr float DEFAULT_COOLER_ON_THRESHOLD_C = 35.0f;
constexpr float COOLER_HYSTERESIS_C = 0.5f;
constexpr unsigned long TEMPERATURE_DATA_TIMEOUT_MS = 15000UL;

float coolerOnThresholdC = DEFAULT_COOLER_ON_THRESHOLD_C;
float latestTemperatureC = NAN;
bool temperatureIsValid = false;
bool coolerIsOn = false;
bool temperatureSensorEnabled = true;
bool staleTemperatureReported = false;
unsigned long lastTemperatureAt = 0;
constexpr unsigned long COOLER_CONFIRM_MS = 5000UL;
constexpr unsigned long COOLER_SAMPLE_GAP_MS = 3000UL;
enum class FanMode : uint8_t { AUTO, ON, OFF };
FanMode fanMode = FanMode::AUTO;
bool coolerChangePending = false;
bool coolerPendingState = false;
unsigned long coolerPendingSince = 0;
unsigned long lastCoolerReportAt = 0;

// ============================== Pumps ==============================

bool extractionPumpOn = false;
bool insertionPumpOn = false;

uint8_t pumpOnLevel()
{
  return PUMP_ACTIVE_HIGH ? HIGH : LOW;
}

uint8_t pumpOffLevel()
{
  return PUMP_ACTIVE_HIGH ? LOW : HIGH;
}

void sendPumpStatus(const __FlashStringHelper *reason)
{
  link.print(F("MegaOutput,PUMP_EXTRACT:"));
  link.print(extractionPumpOn ? F("ON") : F("OFF"));
  link.print(F(",PUMP_INSERT:"));
  link.print(insertionPumpOn ? F("ON") : F("OFF"));
  link.print(F(",REASON:"));
  link.println(reason);
}

void setExtractionPump(bool turnOn, const __FlashStringHelper *reason)
{
  if (turnOn && feederState != FeederState::IDLE) stopFeeder();
  // A single-pump command selects only this channel. BOTH_ON is explicit.
  if (turnOn && insertionPumpOn) {
    digitalWrite(INSERTION_PUMP_PIN, pumpOffLevel());
    insertionPumpOn = false;
  }

  digitalWrite(
    EXTRACTION_PUMP_PIN,
    turnOn ? pumpOnLevel() : pumpOffLevel()
  );
  extractionPumpOn = turnOn;
  sendPumpStatus(reason);
}

void setInsertionPump(bool turnOn, const __FlashStringHelper *reason)
{
  if (turnOn && feederState != FeederState::IDLE) stopFeeder();
  if (turnOn && extractionPumpOn) {
    digitalWrite(EXTRACTION_PUMP_PIN, pumpOffLevel());
    extractionPumpOn = false;
  }

  digitalWrite(
    INSERTION_PUMP_PIN,
    turnOn ? pumpOnLevel() : pumpOffLevel()
  );
  insertionPumpOn = turnOn;
  sendPumpStatus(reason);
}

void startBothPumps(const __FlashStringHelper *reason)
{
  if (feederState != FeederState::IDLE) stopFeeder();
  digitalWrite(EXTRACTION_PUMP_PIN, pumpOnLevel());
  digitalWrite(INSERTION_PUMP_PIN, pumpOnLevel());
  extractionPumpOn = true;
  insertionPumpOn = true;
  sendPumpStatus(reason);
}

void stopAllPumps(const __FlashStringHelper *reason)
{
  digitalWrite(EXTRACTION_PUMP_PIN, pumpOffLevel());
  digitalWrite(INSERTION_PUMP_PIN, pumpOffLevel());
  extractionPumpOn = false;
  insertionPumpOn = false;
  sendPumpStatus(reason);
}

// ============================== Text helpers ==============================

bool charsEqualIgnoreCase(char a, char b)
{
  return toupper((unsigned char)a) == toupper((unsigned char)b);
}

bool startsWithIgnoreCase(const char *text, const char *prefix)
{
  if (text == nullptr || prefix == nullptr) return false;

  while (*prefix != '\0') {
    if (*text == '\0' || !charsEqualIgnoreCase(*text, *prefix)) {
      return false;
    }
    ++text;
    ++prefix;
  }

  return true;
}

char *trimWhitespace(char *text)
{
  while (*text != '\0' && isspace((unsigned char)*text)) ++text;

  char *end = text + strlen(text);
  while (end > text && isspace((unsigned char)end[-1])) --end;
  *end = '\0';
  return text;
}

void toUppercase(char *text)
{
  while (*text != '\0') {
    *text = (char)toupper((unsigned char)*text);
    ++text;
  }
}

bool copyFieldValue(
  const char *message,
  const char *key,
  char *destination,
  size_t destinationSize
)
{
  if (!message || !key || !destination || destinationSize == 0) return false;

  destination[0] = '\0';
  const size_t keyLength = strlen(key);
  const char *field = message;

  while (field && *field != '\0') {
    const char *fieldEnd = strchr(field, ',');
    const char *trimmedField = field;

    while (
      *trimmedField != '\0' &&
      trimmedField != fieldEnd &&
      isspace((unsigned char)*trimmedField)
    ) {
      ++trimmedField;
    }

    size_t fieldLength = fieldEnd
      ? (size_t)(fieldEnd - trimmedField)
      : strlen(trimmedField);

    bool keyMatches = fieldLength >= keyLength;

    if (keyMatches) {
      for (size_t i = 0; i < keyLength; ++i) {
        if (!charsEqualIgnoreCase(trimmedField[i], key[i])) {
          keyMatches = false;
          break;
        }
      }
    }

    if (keyMatches) {
      const char *value = trimmedField + keyLength;
      size_t valueLength = fieldEnd
        ? (size_t)(fieldEnd - value)
        : strlen(value);

      if (valueLength >= destinationSize) valueLength = destinationSize - 1;

      memcpy(destination, value, valueLength);
      destination[valueLength] = '\0';

      char *trimmed = trimWhitespace(destination);
      if (trimmed != destination) {
        memmove(destination, trimmed, strlen(trimmed) + 1);
      }
      return true;
    }

    field = fieldEnd ? fieldEnd + 1 : nullptr;
  }

  return false;
}

// ============================== Servo helpers ==============================

uint16_t angleToPulse(int angle)
{
  angle = constrain(angle, 0, 180);
  return (uint16_t)map(angle, 0, 180, SERVO_MIN_PULSE, SERVO_MAX_PULSE);
}

void setServo(uint8_t channel, int angle)
{
  pwm.setPWM(channel, 0, angleToPulse(angle));
}

void writeCameraServos()
{
  setServo(TILT_CHANNEL, tiltAngle);
  setServo(PAN_CHANNEL, panAngle);
}

void sendServoPosition()
{
  link.print(F("MegaServo,TILT:"));
  link.print(tiltAngle);
  link.print(F(",PAN:"));
  link.println(panAngle);
}

// ============================== Cooler ==============================

uint8_t coolerRelayOnLevel()
{
  return COOLER_RELAY_ACTIVE_LOW ? LOW : HIGH;
}

uint8_t coolerRelayOffLevel()
{
  return COOLER_RELAY_ACTIVE_LOW ? HIGH : LOW;
}

void writeCoolerRelay(bool turnOn)
{
  coolerIsOn = turnOn;
  digitalWrite(
    COOLER_RELAY_PIN,
    turnOn ? coolerRelayOnLevel() : coolerRelayOffLevel()
  );
}

void sendCoolerStatus(const __FlashStringHelper *reason)
{
  link.print(F("MegaOutput,COOLER:"));
  link.print(coolerIsOn ? F("ON") : F("OFF"));
  link.print(F(",FANCTRL:V1,FANMODE:"));
  link.print(fanMode == FanMode::AUTO ? F("AUTO") : fanMode == FanMode::ON ? F("ON") : F("OFF"));
  link.print(F(",FANPENDING:"));
  link.print(coolerChangePending ? (coolerPendingState ? F("ON") : F("OFF")) : F("NONE"));
  link.print(F(",TEMP:"));

  if (temperatureIsValid) link.print(latestTemperatureC, 2);
  else link.print(F("ERROR"));

  link.print(F(",THRESHOLD:"));
  link.print(coolerOnThresholdC, 2);
  link.print(F(",REASON:"));
  link.println(reason);
}

void updateCoolerControl(unsigned long now, bool freshReading = false)
{
  if (!systemEnabled) { writeCoolerRelay(false); return; }
  // Explicit manual modes do not depend on a temperature sensor.
  if (fanMode != FanMode::AUTO) {
    coolerChangePending = false;
    if (coolerIsOn != (fanMode == FanMode::ON)) writeCoolerRelay(fanMode == FanMode::ON);
    return;
  }
  if (!temperatureSensorEnabled) {
    coolerChangePending = false;
    if (coolerIsOn) writeCoolerRelay(false);
    return;
  }
  if (now - lastTemperatureAt > COOLER_SAMPLE_GAP_MS) coolerChangePending = false;
  if (
    temperatureIsValid &&
    (unsigned long)(now - lastTemperatureAt) > TEMPERATURE_DATA_TIMEOUT_MS
  ) {
    temperatureIsValid = false;
    latestTemperatureC = NAN;

    if (coolerIsOn) writeCoolerRelay(false);

    if (!staleTemperatureReported) {
      sendCoolerStatus(F("STALE_TEMPERATURE"));
      staleTemperatureReported = true;
    }
    return;
  }

  if (!temperatureIsValid || !freshReading) return;

  const float offThreshold = coolerOnThresholdC - COOLER_HYSTERESIS_C;
  bool requestedState = coolerIsOn;

  if (!coolerIsOn && latestTemperatureC >= coolerOnThresholdC) {
    requestedState = true;
  }
  else if (coolerIsOn && latestTemperatureC <= offThreshold) {
    requestedState = false;
  }

  if (requestedState == coolerIsOn) {
    coolerChangePending = false;
    return;
  }
  if (!coolerChangePending || coolerPendingState != requestedState) {
    coolerChangePending = true;
    coolerPendingState = requestedState;
    coolerPendingSince = now;
    sendCoolerStatus(F("CONFIRMING_TEMPERATURE"));
  }
  else if (now - coolerPendingSince >= COOLER_CONFIRM_MS) {
    coolerChangePending = false;
    writeCoolerRelay(requestedState);
    sendCoolerStatus(requestedState ? F("HIGH_TEMPERATURE") : F("TEMPERATURE_NORMAL"));
  }
}

void acceptTemperature(float temperatureC, unsigned long now)
{
  if (!temperatureSensorEnabled) return;
  if (isnan(temperatureC) || temperatureC < -55.0f || temperatureC > 125.0f) {
    coolerChangePending = false;
    link.println(F("MegaOutput,ERROR:INVALID_TEMPERATURE"));
    return;
  }

  if (!temperatureIsValid || now - lastTemperatureAt > COOLER_SAMPLE_GAP_MS) coolerChangePending = false;
  latestTemperatureC = temperatureC;
  lastTemperatureAt = now;
  temperatureIsValid = true;
  staleTemperatureReported = false;
  updateCoolerControl(now, true);
}

// ============================== Feeder ==============================

void reportFeeder(const __FlashStringHelper *event, uint32_t job)
{
  link.print(F("MegaOutput,FEED:"));
  link.print(event);
  link.print(F(",JOB:"));
  link.println(job);
}

void stopFeeder()
{
  queuedFeedCycles = 0;
  feederState = FeederState::IDLE;
  setServo(FEEDER_CHANNEL, FEEDER_CLOSED_DEGREES);
  reportFeeder(F("STOPPED"), feederJob);
}

void queueFeedCycles(uint32_t cycles, unsigned long now, uint32_t job = 0)
{
  // Never build an unbounded queue, overlap a feed, or feed during pumping.
  if (cycles < 1 ||
      feederState != FeederState::IDLE || extractionPumpOn || insertionPumpOn) {
    reportFeeder(F("REJECTED"), job);
    return;
  }
  feederJob = job;
  queuedFeedCycles = cycles;
  feederState = FeederState::HOLDING_OPEN;
  feederStateStartedAt = now;
  setServo(FEEDER_CHANNEL, FEEDER_OPEN_DEGREES);
  reportFeeder(F("STARTED"), feederJob);
}

void updateFeeder(unsigned long now)
{
  if (feederState == FeederState::IDLE) return;

  if ((unsigned long)(now - feederStateStartedAt) < FEEDER_POSITION_HOLD_MS) {
    return;
  }

  feederStateStartedAt = now;

  if (feederState == FeederState::HOLDING_OPEN) {
    setServo(FEEDER_CHANNEL, FEEDER_CLOSED_DEGREES);
    feederState = FeederState::HOLDING_CLOSED;
    return;
  }

  if (queuedFeedCycles > 0) --queuedFeedCycles;

  if (queuedFeedCycles == 0) {
    feederState = FeederState::IDLE;
    reportFeeder(F("COMPLETE"), feederJob);
    return;
  }

  setServo(FEEDER_CHANNEL, FEEDER_OPEN_DEGREES);
  feederState = FeederState::HOLDING_OPEN;
}

// ============================== Camera ==============================

void handleCameraCommand(char *cameraCommand, unsigned long now)
{
  toUppercase(cameraCommand);

  if (strcmp(cameraCommand, "SET") == 0 || strcmp(cameraCommand, "NO") == 0) {
    return;
  }

  if ((unsigned long)(now - lastCameraCommandAt) < CAMERA_COMMAND_INTERVAL_MS) {
    return;
  }

  bool recognized = true;

  if (strcmp(cameraCommand, "UP") == 0) {
    tiltAngle = constrain(tiltAngle - CAMERA_STEP_DEGREES, TILT_MIN_DEGREES, TILT_MAX_DEGREES);
  }
  else if (strcmp(cameraCommand, "DOWN") == 0) {
    tiltAngle = constrain(tiltAngle + CAMERA_STEP_DEGREES, TILT_MIN_DEGREES, TILT_MAX_DEGREES);
  }
  else if (strcmp(cameraCommand, "LEFT") == 0) {
    panAngle = constrain(panAngle + CAMERA_STEP_DEGREES, PAN_MIN_DEGREES, PAN_MAX_DEGREES);
  }
  else if (strcmp(cameraCommand, "RIGHT") == 0) {
    panAngle = constrain(panAngle - CAMERA_STEP_DEGREES, PAN_MIN_DEGREES, PAN_MAX_DEGREES);
  }
  else if (strcmp(cameraCommand, "CENTER") == 0) {
    tiltAngle = 90;
    panAngle = 110;
  }
  else {
    recognized = false;
  }

  if (recognized) {
    lastCameraCommandAt = now;
    writeCameraServos();
    sendServoPosition();
  }
}

// A close pulse is held for one second before disabling servo PWM.
void stopSystem(const __FlashStringHelper *reason) {
  const bool wasEnabled = systemEnabled;
  systemEnabled = false;
  stopAllPumps(reason);
  writeCoolerRelay(false);
  fanMode = FanMode::OFF;
  coolerChangePending = false;
  temperatureIsValid = false;
  latestTemperatureC = NAN;
  queuedFeedCycles = 0;
  feederState = FeederState::IDLE;
  pwm.setPWM(PAN_CHANNEL, 0, 4096);
  pwm.setPWM(TILT_CHANNEL, 0, 4096);
  if (wasEnabled) {
    setServo(FEEDER_CHANNEL, FEEDER_CLOSED_DEGREES);
    shutdownServoStarted = millis();
    shutdownServoPending = true;
    reportFeeder(F("STOPPED"), feederJob);
  }
  link.print(F("MegaOutput,SYSTEM:OFF,SYSTEMCTRL:V1,REASON:"));
  link.println(reason);
  sendCoolerStatus(reason);
}
void serviceSystem(uint32_t now) {
  if (systemEnabled && uint32_t(now-lastHostHeartbeat) >= HOST_TIMEOUT_MS)
    stopSystem(F("HOST_TIMEOUT"));
  if (shutdownServoPending && uint32_t(now-shutdownServoStarted) >= FEEDER_POSITION_HOLD_MS) {
    pwm.setPWM(FEEDER_CHANNEL, 0, 4096);
    shutdownServoPending = false;
  }
}

// ============================== Commands ==============================

void handleFormattedMessage(char *message)
{
  message = trimWhitespace(message);
  if (!startsWithIgnoreCase(message, "MegaOutput")) return;

  const unsigned long now = millis();
  char value[28];

  // Process OFF before any actuator field, including combined commands.
  if (copyFieldValue(message, "SYSTEM:", value, sizeof(value))) {
    toUppercase(value);
    if (strcmp(value, "OFF") == 0) { stopSystem(F("COMMAND")); return; }
    if (strcmp(value, "ON") == 0) {
      if (!systemEnabled) {
        systemEnabled = true;
        shutdownServoPending = false;
        fanMode = FanMode::AUTO;
        temperatureIsValid = false;
        coolerChangePending = false;
        setServo(FEEDER_CHANNEL, FEEDER_CLOSED_DEGREES);
      }
      lastHostHeartbeat = now;
      link.println(F("MegaOutput,SYSTEM:ON,SYSTEMCTRL:V1"));
      return;
    }
  }
  if (copyFieldValue(message, "HEARTBEAT:", value, sizeof(value))) {
    if (systemEnabled && strcmp(value, "1") == 0) lastHostHeartbeat = now;
    return;
  }
  if (!systemEnabled) return;

  // Pump command first.
  if (copyFieldValue(message, "PUMP:", value, sizeof(value))) {
    toUppercase(value);

    if (strcmp(value, "BOTH_ON") == 0) {
      startBothPumps(F("COMMAND"));
    }
    else if (strcmp(value, "EXTRACT_ON") == 0) {
      setExtractionPump(true, F("COMMAND"));
    }
    else if (strcmp(value, "EXTRACT_OFF") == 0) {
      setExtractionPump(false, F("COMMAND"));
    }
    else if (strcmp(value, "INSERT_ON") == 0) {
      setInsertionPump(true, F("COMMAND"));
    }
    else if (strcmp(value, "INSERT_OFF") == 0) {
      setInsertionPump(false, F("COMMAND"));
    }
    else if (strcmp(value, "STOP_ALL") == 0) {
      stopAllPumps(F("COMMAND"));
    }
  }

  if (copyFieldValue(message, "TEMPSENSOR:", value, sizeof(value))) {
    toUppercase(value);
    if (strcmp(value, "ON") == 0 || strcmp(value, "OFF") == 0) {
      bool enabled = strcmp(value, "ON") == 0;
      if (enabled != temperatureSensorEnabled) {
        temperatureSensorEnabled = enabled;
        temperatureIsValid = false;
        latestTemperatureC = NAN;
        staleTemperatureReported = false;
        coolerChangePending = false;
        if (fanMode == FanMode::AUTO) writeCoolerRelay(false);
      }
      link.print(F("MegaOutput,TEMPSENSOR:"));
      link.println(temperatureSensorEnabled ? F("ON") : F("OFF"));
      sendCoolerStatus(temperatureSensorEnabled ? F("WAITING_TEMPERATURE") : F("SENSOR_DISABLED"));
    }
  }

  if (copyFieldValue(message, "FANMODE:", value, sizeof(value))) {
    toUppercase(value);
    if (strcmp(value, "AUTO") == 0 || strcmp(value, "ON") == 0 || strcmp(value, "OFF") == 0) {
      FanMode requested = strcmp(value, "AUTO") == 0 ? FanMode::AUTO :
                          strcmp(value, "ON") == 0 ? FanMode::ON : FanMode::OFF;
      if (requested != fanMode) {
        fanMode = requested;
        coolerChangePending = false;
        temperatureIsValid = false; // Auto resumes with new readings.
        writeCoolerRelay(fanMode == FanMode::ON);
      }
      sendCoolerStatus(F("FAN_MODE_COMMAND"));
    }
  }

  // Read cooler threshold before temperature.
  if (copyFieldValue(message, "COOLERTHRESHOLD:", value, sizeof(value))) {
    char *end = nullptr;
    const float parsedThreshold = strtod(value, &end);

    if (
      end != value &&
      *trimWhitespace(end) == '\0' &&
      parsedThreshold >= -54.0f &&
      parsedThreshold <= 125.0f
    ) {
      if (coolerOnThresholdC != parsedThreshold) coolerChangePending = false;
      coolerOnThresholdC = parsedThreshold;
    }
  }

  if (copyFieldValue(message, "TEMP:", value, sizeof(value))) {
    char *end = nullptr;
    const float parsedTemperature = strtod(value, &end);

    if (end != value && *trimWhitespace(end) == '\0') {
      acceptTemperature(parsedTemperature, now);
    } else { coolerChangePending = false; }
  }

  char cameraCommand[12];
  if (copyFieldValue(message, "CAMERA:", cameraCommand, sizeof(cameraCommand))) {
    toUppercase(cameraCommand);

    if (strcmp(cameraCommand, "SET") == 0) {
      if (copyFieldValue(message, "TILT:", value, sizeof(value))) {
        tiltAngle = constrain(atoi(value), TILT_MIN_DEGREES, TILT_MAX_DEGREES);
      }

      if (copyFieldValue(message, "PAN:", value, sizeof(value))) {
        panAngle = constrain(atoi(value), PAN_MIN_DEGREES, PAN_MAX_DEGREES);
      }

      writeCameraServos();
      sendServoPosition();
    }
    else if (strcmp(cameraCommand, "NO") != 0) {
      handleCameraCommand(cameraCommand, now);
    }
  }

  char feedCommand[16];
  if (copyFieldValue(message, "FEED:", feedCommand, sizeof(feedCommand))) {
    toUppercase(feedCommand);

    if (strcmp(feedCommand, "STOP") == 0) {
      stopFeeder();
      reportFeeder(F("STOPPED"), 0);
    }
    else if (strcmp(feedCommand, "CUSTOM") == 0) {
      uint32_t job = 0;
      if (copyFieldValue(message, "JOB:", value, sizeof(value))) {
        job = strtoul(value, nullptr, 10);
      }
      if (copyFieldValue(message, "CYCLES:", value, sizeof(value))) {
        char *end = nullptr;
        errno = 0;
        unsigned long cycles = strtoul(value, &end, 10);
        bool digitsOnly = value[0] != '\0';
        for (const char *p = value; *p; ++p) {
          if (!isdigit((unsigned char)*p)) digitsOnly = false;
        }
        if (digitsOnly && errno != ERANGE && end != value && *end == '\0' && cycles >= 1 && cycles <= UINT32_MAX) {
          queueFeedCycles((uint32_t)cycles, now, job);
        } else {
          reportFeeder(F("REJECTED"), job);
        }
      } else {
        reportFeeder(F("REJECTED"), job);
      }
    }
    else if (strcmp(feedCommand, "ONE") == 0) {
      queueFeedCycles(1, now);
    }
    else if (
      strcmp(feedCommand, "SCHEDULED") == 0 ||
      strcmp(feedCommand, "YES") == 0
    ) {
      queueFeedCycles(SCHEDULED_FEED_CYCLES, now);
    }
  }
}

// ============================== Arduino ==============================

void setup()
{
  // Fail-off startup: set the output latch to OFF before enabling each pin.
  digitalWrite(COOLER_RELAY_PIN, coolerRelayOffLevel());
  pinMode(COOLER_RELAY_PIN, OUTPUT);
  writeCoolerRelay(false);

  digitalWrite(EXTRACTION_PUMP_PIN, pumpOffLevel());
  digitalWrite(INSERTION_PUMP_PIN, pumpOffLevel());
  pinMode(EXTRACTION_PUMP_PIN, OUTPUT);
  pinMode(INSERTION_PUMP_PIN, OUTPUT);
  extractionPumpOn = false;
  insertionPumpOn = false;

  Wire.begin();
#if defined(WIRE_HAS_TIMEOUT)
  Wire.setWireTimeout(2500, true); // Bound a stuck I2C transaction to 2.5 ms.
#endif

  pwm.begin();
  pwm.setPWMFreq(50);
  delay(10);

  // PCA9685 can stay powered through a Mega reset; explicitly clear PWM.
  pwm.setPWM(PAN_CHANNEL, 0, 4096);
  pwm.setPWM(TILT_CHANNEL, 0, 4096);
  pwm.setPWM(FEEDER_CHANNEL, 0, 4096);

  link.println(F("MegaOutput,BOOT:1,FEEDER:V3,FANCTRL:V1,SYSTEMCTRL:V1,SYSTEM:OFF"));
  sendServoPosition();
  sendCoolerStatus(F("WAITING_FOR_TEMPERATURE"));
  sendPumpStatus(F("READY"));
}

} // namespace MegaOutput
namespace MegaInput {

// ========================== PIN DEFINITIONS ==========================

constexpr uint8_t waterTempSensorPin = 4;
constexpr uint8_t turbiditySensorPin = A0;
constexpr uint8_t phSensorPin = A1;

constexpr uint8_t floatSensorUpperMostPin = 7;
constexpr uint8_t floatSensorNormalPin = 8;
constexpr uint8_t floatSensorLowerMostPin = 9;

// Declare this type before the first function so Arduino-generated prototypes compile.
// Same bit order as sensor_control.SENSORS in the Raspberry Pi application.
enum SensorBit : uint8_t { TEMP_BIT, PH_BIT, TURBIDITY_BIT, LOW_BIT, NORMAL_BIT, UPPER_BIT };

// ========================== TURBIDITY ==========================
constexpr uint8_t TURBIDITY_SAMPLE_COUNT = 100;
constexpr uint16_t TURBIDITY_SAMPLE_MS = 5; // 500 ms per batch; never delay().

// Known-NTU reference solutions are required; a 0-1000 NTU product listing
// does NOT specify the ADC transfer curve. Replace BOTH placeholder pairs
// with measured 100-sample ADC means and their certified NTU values.
// Add intermediate measured points if available, in increasing NTU order.
// ADC must decrease as NTU rises. These points are independent of the web's
// named clarity ranges, which are still used for display and fail-safe.
struct TurbidityPoint { float adc, ntu; };
constexpr TurbidityPoint TURBIDITY_NTU_POINTS[] = {
  {-1.0f, -1.0f}, // {measured ADC in lower-NTU reference, reference NTU}
  {-1.0f, -1.0f}  // {measured ADC in higher-NTU reference, reference NTU}
};
constexpr uint8_t TURBIDITY_NTU_POINT_COUNT =
  sizeof(TURBIDITY_NTU_POINTS) / sizeof(TURBIDITY_NTU_POINTS[0]);
static_assert(TURBIDITY_NTU_POINT_COUNT >= 2 && TURBIDITY_NTU_POINT_COUNT <= 8,
              "Use 2 to 8 measured NTU reference points");

bool validTurbidityCalibration(const TurbidityPoint *points, uint8_t count) {
  if (count < 2 || count > 8) return false;
  for (uint8_t i = 0; i < count; ++i) {
    if (!isfinite(points[i].adc) || !isfinite(points[i].ntu) ||
        points[i].adc < 0 || points[i].adc > 1023 ||
        points[i].ntu < 0 || points[i].ntu > 1000) return false;
    if (i && (points[i-1].adc <= points[i].adc ||
              points[i-1].ntu >= points[i].ntu)) return false;
  }
  return true;
}
float estimateTurbidityNTU(float adc, const TurbidityPoint *points, uint8_t count) {
  if (!validTurbidityCalibration(points, count) || !isfinite(adc) ||
      adc > points[0].adc || adc < points[count-1].adc) return NAN;
  for (uint8_t i = 1; i < count; ++i) {
    if (adc >= points[i].adc) {
      // Piecewise linear estimate between real measurements; no extrapolation.
      const float fraction = (points[i-1].adc - adc) / (points[i-1].adc - points[i].adc);
      return points[i-1].ntu + fraction * (points[i].ntu - points[i-1].ntu);
    }
  }
  return NAN;
}

// ========================== pH CALIBRATION ==========================

// Fill these two ADC constants with YOUR stable raw-ADC averages in each buffer.
// 0.0f means not entered yet. Never substitute the old 4.01 buffer's ADC here.
constexpr float PH_BUFFER_686 = 6.86f;
constexpr float PH_BUFFER_918 = 9.18f;
constexpr float PH_ADC_686 = 0.0f;  // EDIT: your measured ADC in pH 6.86 buffer.
constexpr float PH_ADC_918 = 0.0f;  // EDIT: your measured ADC in pH 9.18 buffer.

// This PH-4502C setup's ADC decreases as pH increases.
constexpr bool PH_CALIBRATION_VALID =
  PH_ADC_686 > 0.0f && PH_ADC_686 < 1023.0f &&
  PH_ADC_918 > 0.0f && PH_ADC_918 < 1023.0f &&
  PH_ADC_686 - PH_ADC_918 >= 20.0f;
// Compute the scale once at compile time, rather than divide on each report.
constexpr float PH_PER_ADC = PH_CALIBRATION_VALID ?
  (PH_BUFFER_918 - PH_BUFFER_686) / (PH_ADC_918 - PH_ADC_686) : 0.0f;

float runtimePh686 = PH_ADC_686, runtimePh918 = PH_ADC_918;
float runtimePhScale = PH_PER_ADC;
bool runtimePhValid = PH_CALIBRATION_VALID;

constexpr uint8_t PH_SAMPLE_COUNT = 15;
constexpr uint8_t PH_TRIM_COUNT = 3;    // Average the middle 9; reject 6 outliers.
constexpr float PH_EMA_ALPHA = 0.20f;   // More smoothing = slower response.
static_assert(PH_TRIM_COUNT * 2 < PH_SAMPLE_COUNT, "Invalid pH trimming");
static_assert(PH_EMA_ALPHA > 0.0f && PH_EMA_ALPHA <= 1.0f, "Invalid pH smoothing");
float phRawADC = 0.0f;                 // Trimmed mean, BEFORE exponential smoothing.
bool phRawMode = false;               // Read-only bench diagnostics; never saves calibration.
bool phRawReportPending = false;

void writePhCalibrationStatus(Print &out) {
  out.print(F("MegaInputStatus,PHCAL:CODE_CONSTANTS,PH_PIN:A1,CALIBRATED:"));
  out.print(PH_CALIBRATION_VALID ? 1 : 0);
  if (PH_CALIBRATION_VALID) {
    out.print(F(",PH686_ADC:")); out.print(PH_ADC_686, 2);
    out.print(F(",PH918_ADC:")); out.print(PH_ADC_918, 2);
  } else {
    out.print(F(",ACTION:SET_PH_ADC_CONSTANTS"));
  }
  out.println();
}

// ========================== TEMPERATURE ==========================

OneWire oneWire(waterTempSensorPin);
DallasTemperature waterTempSensor(&oneWire);

// ========================== SENSOR MODE ==========================

// NORMAL       : temperature + floats + turbidity + pH
// AUTO_QUALITY : floats + turbidity + pH; temperature paused while extracting
// AUTO_EXTRACT : legacy alias behavior; floats + turbidity only
// FLOAT_ONLY   : floats only (used during insertion/manual pumping)
enum class SensorMode : uint8_t {
  NORMAL,
  AUTO_QUALITY,
  AUTO_EXTRACT,
  FLOAT_ONLY
};

SensorMode sensorMode = SensorMode::NORMAL;

uint8_t sensorMask = 63;
uint32_t sensorRevision = 0;
bool lowTriggered = false;
bool normalTriggered = false;
bool upperTriggered = false;
bool floatsReady = false;

bool sensorEnabled(SensorBit bit) { return (sensorMask & (1U << bit)) != 0; }

// Cooperative sampling: no delay(), heap Strings, or UART-buffer waits in loop.
// OneWire/ADC transactions still take a short, finite time on the CPU.
bool systemEnabled = false;
uint32_t lastHostHeartbeat = 0;
constexpr uint32_t HOST_TIMEOUT_MS = 4000;
bool modeStatusPending = false, configStatusPending = false, systemStatusPending = false;
float waterTempC = 0, phAverageADC = 0; // Smoothed pH ADC for display/control.
bool tempReady = false, tempValid = false, phReadingReady = false;
const __FlashStringHelper *floatValue = nullptr;
const __FlashStringHelper *turbidityValue = nullptr;

constexpr uint32_t tempInterval = 2000, floatInterval = 250;
constexpr uint32_t turbidityInterval = 2500, phInterval = 2000, serialInterval = 500;
constexpr uint32_t TEMP_CONVERSION_MS = 750; // DS18B20, 12-bit.
uint32_t lastTempRead, lastFloatRead, lastTurbidityRead, lastPhRead, lastSerialSend;
uint32_t tempStartedAt, lastAdcSample;
bool tempPending = false;
DeviceAddress temperatureAddress;
bool temperatureFound = false;
enum class AdcTask : uint8_t { IDLE, PH, TURBIDITY };
AdcTask adcTask = AdcTask::IDLE;
uint16_t phSamples[PH_SAMPLE_COUNT];
uint32_t turbidityTotal = 0; // 100 * 1023 = 102300: must NOT be uint16_t.
uint16_t turbidityRawADC = 0; // 100-sample mean, retained for the Pi protocol.
float turbidityMeanADC = 0, turbidityNTU = NAN;
bool turbidityReportPending = false;
bool turbidityReady = false;
uint8_t sampleCount = 0;

// One complete snapshot, drained in small chunks as UART space becomes free.
// Status replies use the same buffer so they cannot split a sensor line.
class PacketWriter : public Print {
public:
  uint8_t data[256];
  uint16_t length = 0, position = 0;
  bool overflow = false;
  size_t write(uint8_t byte) override {
    if (length >= sizeof(data)) { overflow = true; return 0; }
    data[length++] = byte;
    return 1;
  }
  bool busy() const { return position < length; }
  void beginPacket() { length = position = 0; overflow = false; }
  void finishPacket() {
    if (overflow) { // Never send a truncated packet as a valid sensor frame.
      beginPacket();
      println(F("MegaInputStatus,ERROR:PACKET_TOO_LONG"));
    }
  }
  void drain() {
    if (busy()) {
      link.write(data + position, length - position);
      position = length;
    }
  }

};
PacketWriter packet;

bool phActive() {
  return phRawMode || (sensorEnabled(PH_BIT) &&
    (sensorMode == SensorMode::NORMAL || sensorMode == SensorMode::AUTO_QUALITY));
}
bool turbidityActive() {
  return !phRawMode && sensorEnabled(TURBIDITY_BIT) &&
         sensorMode != SensorMode::FLOAT_ONLY;
}
void resetAcquisition() {
  const uint32_t now = millis();
  tempReady = tempValid = phReadingReady = floatsReady = false;
  turbidityReady = false;
  turbidityReportPending = false;
  turbidityTotal = 0; turbidityMeanADC = 0; turbidityNTU = NAN;
  phRawADC = phAverageADC = 0.0f;
  phRawReportPending = false;
  floatValue = turbidityValue = F("WAITING");
  tempPending = false; adcTask = AdcTask::IDLE; sampleCount = 0;
  lastTempRead = now - tempInterval;
  lastPhRead = now - phInterval;
  lastTurbidityRead = now - turbidityInterval;
  lastFloatRead = now - floatInterval;
}

void writeModeStatus(Print &out) {
  out.print(F("MegaInputStatus,MODE:"));
  out.println(sensorMode == SensorMode::NORMAL ? F("NORMAL") :
    sensorMode == SensorMode::AUTO_QUALITY ? F("AUTO_QUALITY") :
    sensorMode == SensorMode::AUTO_EXTRACT ? F("AUTO_EXTRACT") : F("FLOAT_ONLY"));
}
void handleCommand(char *message) {
  // Commands are ASCII; normalize only text, not numeric conversion semantics.
  for (char *p = message; *p; ++p) *p = toupper((unsigned char)*p);

  // Optional read-only pH ADC view for measuring your buffers with main.py stopped.
  // There are no capture/save commands and no EEPROM reads or writes.
  if (strcmp_P(message, PSTR("MEGAINPUT,PHRAW:START")) == 0) {
    systemEnabled = false;
    phRawMode = true;
    resetAcquisition();
    return;
  }
  if (strcmp_P(message, PSTR("MEGAINPUT,PHRAW:STOP")) == 0 ||
      strcmp_P(message, PSTR("MEGAINPUT,SYSTEM:OFF")) == 0) {
    systemEnabled = phRawMode = false;
    resetAcquisition();
    systemStatusPending = true;
    return;
  }
  // A Pi auto-arm must not interrupt a deliberate bench measurement session.
  if (phRawMode) return;

  if (strcmp_P(message, PSTR("MEGAINPUT,SYSTEM:OFF")) == 0 ||
      strcmp_P(message, PSTR("MEGAINPUT,SYSTEM:ON")) == 0) {
    bool requested = strcmp_P(message, PSTR("MEGAINPUT,SYSTEM:ON")) == 0;
    if (requested != systemEnabled) { systemEnabled = requested; resetAcquisition(); }
    if (requested) lastHostHeartbeat = millis();
    systemStatusPending = true;
    return;
  }
  if (strcmp_P(message, PSTR("MEGAINPUT,HEARTBEAT:1")) == 0) {
    if (systemEnabled) lastHostHeartbeat = millis();
    return;
  }
  if ((strncmp_P(message, PSTR("MEGAINPUT,SENSORS:"), 17) == 0)) {
    char *number = strchr(message, ':') + 1, *end;
    if (!isdigit((unsigned char)*number)) return;
    errno = 0;
    unsigned long mask = strtoul(number, &end, 10);
    if (errno == ERANGE || mask > 63 || strncmp_P(end, PSTR(",REV:"), 5) != 0) return;
    char *revStart = end + 5;
    if (!isdigit((unsigned char)*revStart)) return;
    errno = 0;
    unsigned long revision = strtoul(revStart, &end, 10);
    if (errno == ERANGE || *end || revision > UINT32_MAX) return;
    if (sensorMask != mask || sensorRevision != revision) {
      sensorMask = mask; sensorRevision = revision; resetAcquisition();
    }
    configStatusPending = true;
    return;
  }
  if (!(strncmp_P(message, PSTR("MEGAINPUT,MODE:"), 14) == 0)) return;
  const char *value = strchr(message, ':') + 1;
  SensorMode next;
  if (strcmp_P(value, PSTR("NORMAL")) == 0) next = SensorMode::NORMAL;
  else if (strcmp_P(value, PSTR("AUTO_QUALITY")) == 0) next = SensorMode::AUTO_QUALITY;
  else if (strcmp_P(value, PSTR("AUTO_EXTRACT")) == 0) next = SensorMode::AUTO_EXTRACT;
  else if (strcmp_P(value, PSTR("FLOAT_ONLY")) == 0) next = SensorMode::FLOAT_ONLY;
  else return;
  if (next != sensorMode) { sensorMode = next; resetAcquisition(); }
  modeStatusPending = true;
}

float calculatePH(float adc) {
  if (!runtimePhValid || !isfinite(adc)) return NAN;
  // Anchored form passes through both measured points without rounding ADC first.
  return PH_BUFFER_686 + (adc - runtimePh686) * runtimePhScale;
}

void serviceAnalog(uint32_t now) {
  if (adcTask == AdcTask::IDLE) {
    if (phActive() && uint32_t(now-lastPhRead) >= phInterval) {
      adcTask = AdcTask::PH; lastPhRead = now; sampleCount = 0;
      analogRead(phSensorPin); // Discard first conversion after ADC mux switch.
      lastAdcSample = now;
    } else if (turbidityActive() && uint32_t(now-lastTurbidityRead) >= turbidityInterval) {
      adcTask = AdcTask::TURBIDITY; lastTurbidityRead = now;
      sampleCount = 0; turbidityTotal = 0;
      analogRead(turbiditySensorPin);
      lastAdcSample = now;
    }
    return;
  }
  if (uint32_t(now-lastAdcSample) < (adcTask == AdcTask::PH ? 20U : TURBIDITY_SAMPLE_MS)) return;
  lastAdcSample = now;
  if (adcTask == AdcTask::PH) {
    // Insertion-sort each new sample: at most 14 moves, never a wait loop.
    uint16_t value = analogRead(phSensorPin);
    uint8_t index = sampleCount;
    while (index && phSamples[index-1] > value) {
      phSamples[index] = phSamples[index-1]; --index;
    }
    phSamples[index] = value;
    if (++sampleCount == PH_SAMPLE_COUNT) {
      uint16_t total = 0;
      for (uint8_t i = PH_TRIM_COUNT; i < PH_SAMPLE_COUNT - PH_TRIM_COUNT; ++i)
        total += phSamples[i];
      phRawADC = total / float(PH_SAMPLE_COUNT - 2 * PH_TRIM_COUNT);
      if (!phReadingReady) phAverageADC = phRawADC;
      else phAverageADC += PH_EMA_ALPHA * (phRawADC - phAverageADC);
      phReadingReady = true;
      if (phRawMode) phRawReportPending = true;
      adcTask = AdcTask::IDLE;
    }
  } else {
    turbidityTotal += analogRead(turbiditySensorPin);
    if (++sampleCount == TURBIDITY_SAMPLE_COUNT) {
      turbidityMeanADC = turbidityTotal / float(TURBIDITY_SAMPLE_COUNT);
      turbidityNTU = estimateTurbidityNTU(turbidityMeanADC,
        TURBIDITY_NTU_POINTS, TURBIDITY_NTU_POINT_COUNT);
      const uint16_t raw = turbidityTotal / TURBIDITY_SAMPLE_COUNT;
      turbidityRawADC = raw;
      turbidityReady = true;
      turbidityReportPending = true;
      // Exact integer equivalents of the previous ADC-to-clarity thresholds.
      turbidityValue = raw <= 170 ? F("Very Cloudy") : raw <= 409 ? F("Cloudy") :
        raw <= 750 ? F("Slightly Cloudy") : raw <= 954 ? F("Clear") : F("Very Clear");
      adcTask = AdcTask::IDLE;
    }
  }
}
void serviceTemperature(uint32_t now) {
  if (!sensorEnabled(TEMP_BIT) || sensorMode != SensorMode::NORMAL) return;
  if (tempPending) {
    if (uint32_t(now-tempStartedAt) < TEMP_CONVERSION_MS) return;
    waterTempC = waterTempSensor.getTempC(temperatureAddress);
    tempValid = isfinite(waterTempC) && waterTempC != DEVICE_DISCONNECTED_C &&
      waterTempC >= -55 && waterTempC <= 125;
    // Preserve the attached probe offset, after checking the disconnect sentinel.
    if (tempValid) waterTempC -= 0.2f;
    tempReady = true; tempPending = false;
    // Rediscover on the next acquisition if the probe was replaced/disconnected.
    if (!tempValid) temperatureFound = false;
  } else if (uint32_t(now-lastTempRead) >= tempInterval) {
    lastTempRead = now;
    if (!temperatureFound) temperatureFound = waterTempSensor.getAddress(temperatureAddress, 0);
    if (!temperatureFound) { tempReady = true; tempValid = false; return; }
    waterTempSensor.requestTemperatures(); // Async; conversion happens in the probe.
    tempStartedAt = millis(); tempPending = true;
  }
}

void readWaterFloat()
{
  upperTriggered = sensorEnabled(UPPER_BIT) && digitalRead(floatSensorUpperMostPin) == LOW;
  normalTriggered = sensorEnabled(NORMAL_BIT) && digitalRead(floatSensorNormalPin) == LOW;
  lowTriggered = sensorEnabled(LOW_BIT) && digitalRead(floatSensorLowerMostPin) == LOW;
  floatsReady = true;

  if (upperTriggered) {
    floatValue = F("Water Level is Overflowing");
  }
  else if (normalTriggered) {
    floatValue = F("Water Level is Normal");
  }
  else if (sensorEnabled(LOW_BIT) && !lowTriggered) {
    floatValue = F("Water Level is Very Low");
  }
  else if (lowTriggered && sensorEnabled(NORMAL_BIT)) {
    floatValue = F("Water Level is Low");
  }
  else {
    floatValue = (sensorMask & 56) ? F("PARTIAL") : F("DISABLED");
  }
}


void writeFloatField(SensorBit bit, bool value) {
  if (!sensorEnabled(bit)) packet.print(F("DISABLED"));
  else if (!floatsReady) packet.print(F("WAITING"));
  else packet.print(value ? 1 : 0);
}
void sendSerialToRaspberryPi() {
  packet.beginPacket();
  packet.print(F("MegaInput,Temp:"));
  if (!sensorEnabled(TEMP_BIT)) packet.print(F("DISABLED"));
  else if (sensorMode != SensorMode::NORMAL) packet.print(F("PAUSED"));
  else if (!tempReady) packet.print(F("WAITING"));
  else if (!tempValid) packet.print(F("ERROR"));
  else packet.print(waterTempC, 2);
  packet.print(F(",FloatSensor:")); packet.print(floatValue);
  packet.print(F(",Turbidity:"));
  if (!sensorEnabled(TURBIDITY_BIT)) packet.print(F("DISABLED"));
  else if (!turbidityActive()) packet.print(F("PAUSED"));
  else packet.print(turbidityValue);
  packet.print(F(",TurbidityADC:"));
  if (!sensorEnabled(TURBIDITY_BIT)) packet.print(F("DISABLED"));
  else if (!turbidityActive()) packet.print(F("PAUSED"));
  else if (!turbidityReady) packet.print(F("WAITING"));
  else packet.print(turbidityRawADC);
  packet.print(F(",pH:"));
  if (!sensorEnabled(PH_BIT)) packet.print(F("DISABLED"));
  else if (!phActive()) packet.print(F("PAUSED"));
  else if (!phReadingReady || !runtimePhValid) packet.print(F("WAITING"));
  else packet.print(calculatePH(phAverageADC), 2);
  packet.print(F(",pHADC:"));
  if (!sensorEnabled(PH_BIT)) packet.print(F("DISABLED"));
  else if (!phActive()) packet.print(F("PAUSED"));
  else if (!phReadingReady) packet.print(F("WAITING"));
  else packet.print(phRawADC, 2); // Unsmoothed for troubleshooting.
  packet.print(F(",FloatLow:")); writeFloatField(LOW_BIT, lowTriggered);
  packet.print(F(",FloatNormal:")); writeFloatField(NORMAL_BIT, normalTriggered);
  packet.print(F(",FloatUpper:")); writeFloatField(UPPER_BIT, upperTriggered);
  packet.print(F(",SENSORCFG:")); packet.println(sensorRevision);
  packet.finishPacket();
}
void serviceTransmit(uint32_t now) {
  if (!packet.busy()) {
    packet.beginPacket();
    if (phRawMode && phRawReportPending) {
      phRawReportPending = false;
      packet.print(F("MegaInputStatus,PHRAW:LIVE,pHADC:"));
      packet.print(phRawADC, 2);
      packet.print(F(",pH:"));
      if (runtimePhValid) packet.println(calculatePH(phAverageADC), 2);
      else packet.println(F("WAITING"));
    } else if (systemStatusPending ||
               (!systemEnabled && !phRawMode && uint32_t(now-lastSerialSend) >= 2000)) {
      systemStatusPending = false; lastSerialSend = now;
      packet.print(F("MegaInputStatus,SYSTEM:")); packet.println(systemEnabled ? F("ON") : F("OFF"));
    } else if (configStatusPending) {
      configStatusPending = false;
      packet.print(F("MegaInputStatus,SENSORCFG:")); packet.println(sensorRevision);
    } else if (modeStatusPending) {
      modeStatusPending = false; writeModeStatus(packet);
    } else if (systemEnabled && turbidityActive() && turbidityReportPending) {
      turbidityReportPending = false;
      // A separate whole frame preserves the existing 256-byte sensor packet.
      packet.print(F("MegaInputStatus,TURBIDITY:LIVE,rawAdc:"));
      packet.print(turbidityMeanADC, 2);
      packet.print(F(",NTU:"));
      if (!validTurbidityCalibration(TURBIDITY_NTU_POINTS, TURBIDITY_NTU_POINT_COUNT))
        packet.print(F("UNCALIBRATED"));
      else if (!isfinite(turbidityNTU)) packet.print(F("OUT_OF_RANGE"));
      else packet.print(turbidityNTU, 2);
      packet.print(F(",SAMPLES:")); packet.print(TURBIDITY_SAMPLE_COUNT);
      packet.println(F(",NTU_METHOD:LINEAR_ESTIMATE"));
    } else if (systemEnabled && uint32_t(now-lastSerialSend) >= serialInterval) {
      lastSerialSend = now; sendSerialToRaspberryPi();
    }
  }
  packet.drain();
}
void setup() {
  waterTempSensor.begin();
  waterTempSensor.setWaitForConversion(false);
  temperatureFound = waterTempSensor.getAddress(temperatureAddress, 0);
  pinMode(floatSensorUpperMostPin, INPUT_PULLUP);
  pinMode(floatSensorNormalPin, INPUT_PULLUP);
  pinMode(floatSensorLowerMostPin, INPUT_PULLUP);
  pinMode(turbiditySensorPin, INPUT); pinMode(phSensorPin, INPUT);
  analogReference(DEFAULT);
  resetAcquisition();
  // Boot diagnostics only; runtime output is buffered and drained without waits.
  link.println(F("MegaInputStatus,BOOT:1,INPUTFW:ASYNC_V1,SYSTEMCTRL:V1,SYSTEM:OFF"));
  writeModeStatus(link);
  writePhCalibrationStatus(link);
}
} // namespace MegaInput

namespace Policy {
struct Range { uint16_t low, high; uint8_t category; };
struct Settings {
  uint32_t revision, confirmMs, settleMs;
  float low, high, warningLow, warningHigh, adc686, adc918, fanThreshold;
  bool maintenance;
  uint8_t count;
  Range ranges[20];
};
Settings active{}, pending{};
bool configured = false, staging = false, autoMode = false;
uint32_t receivedRows = 0;
uint8_t quality = 0; // 0 none, 1 turbidity, 2 pH.
uint8_t levelCorrection = 0; // 0 none, 1 overflow, 2 low.
uint8_t phCandidate = 0;
bool turbCandidate = false;
uint32_t phSince = 0, turbSince = 0;
bool settling = true, settlingClock = false;
uint32_t settlingSince = 0;
const char *mode = "manual_idle";
uint32_t lastReport = 0;

bool number(const char *message, const char *key, float &value, float low, float high) {
  char text[28], *end;
  if (!MegaOutput::copyFieldValue(message, key, text, sizeof(text))) return false;
  value = strtod(text, &end);
  return end != text && !*end && isfinite(value) && value >= low && value <= high;
}
bool integer(const char *message, const char *key, uint32_t &value, uint32_t maximum = UINT32_MAX) {
  char text[28], *end;
  if (!MegaOutput::copyFieldValue(message, key, text, sizeof(text)) || !isdigit(text[0])) return false;
  errno = 0; value = strtoul(text, &end, 10);
  return !errno && !*end && value <= maximum;
}
void report(uint32_t now) {
  lastReport = now;
  link.print(F("AquaPiMega,WATER:")); link.print(mode);
  link.print(F(",EXTRACT:")); link.print(MegaOutput::extractionPumpOn ? 1 : 0);
  link.print(F(",INSERT:")); link.print(MegaOutput::insertionPumpOn ? 1 : 0);
  link.print(F(",MANUAL:")); link.print(autoMode ? 0 : 1);
  link.print(F(",MODE:"));
  link.print(MegaInput::sensorMode == MegaInput::SensorMode::NORMAL ? F("NORMAL") :
             MegaInput::sensorMode == MegaInput::SensorMode::AUTO_QUALITY ? F("AUTO_QUALITY") : F("FLOAT_ONLY"));
  link.print(F(",SETTLING:")); link.print(settling && quality != 2 ? 1 : 0);
  link.print(F(",REMAIN:"));
  if (!settlingClock) link.println(-1);
  else link.println(uint32_t(now-settlingSince) >= active.settleMs ? 0UL :
                    (active.settleMs - uint32_t(now-settlingSince) + 999UL)/1000UL);
}
void resetQuality() { quality = phCandidate = 0; turbCandidate = false; }
void change(bool extract, bool insert, const char *next, uint32_t now) {
  bool changed = MegaOutput::extractionPumpOn != extract || MegaOutput::insertionPumpOn != insert || strcmp(mode,next);
  if (MegaOutput::extractionPumpOn != extract || MegaOutput::insertionPumpOn != insert) {
    if (extract && insert) MegaOutput::startBothPumps(F("LOCAL_FAILSAFE"));
    else if (extract) MegaOutput::setExtractionPump(true,F("LOCAL_FAILSAFE"));
    else if (insert) MegaOutput::setInsertionPump(true,F("LOCAL_FAILSAFE"));
    else MegaOutput::stopAllPumps(F("LOCAL_FAILSAFE"));
  }
  mode = next;
  MegaInput::SensorMode desired = quality && autoMode ? MegaInput::SensorMode::AUTO_QUALITY :
    extract || insert ? MegaInput::SensorMode::FLOAT_ONLY : MegaInput::SensorMode::NORMAL;
  if (MegaInput::sensorMode != desired) {
    bool qualityChange = MegaInput::sensorMode == MegaInput::SensorMode::FLOAT_ONLY || desired == MegaInput::SensorMode::FLOAT_ONLY;
    MegaInput::sensorMode = desired;
    if (qualityChange) MegaInput::resetAcquisition();
    else { MegaInput::tempPending=false; MegaInput::tempReady=false; MegaInput::tempValid=false; }
    MegaInput::modeStatusPending = true;
  }
  if (extract || insert) { settling = true; settlingClock = false; }
  if (changed) report(now);
}
void manual() {
  autoMode = false; resetQuality(); levelCorrection = 0;
}
void stopped() {
  manual(); settling = true; settlingClock = false;
  change(false,false,"manual_idle",millis());
}
void ack(uint32_t revision) { link.print(F("AquaPiMega,CFG:")); link.println(revision); }
void configure(const char *message) {
  uint32_t revision, n;
  if (integer(message,"BEGIN:",revision)) {
    if (configured && revision == active.revision) { ack(revision); return; }
    staging = false; configured = false; receivedRows = 0; resetQuality();
    if (autoMode) change(false,false,"idle",millis());
    Settings next{}; next.revision = revision;
    if (!number(message,"PL:",next.low,0,14) || !number(message,"PH:",next.high,0,14) ||
        !number(message,"WL:",next.warningLow,0,14) || !number(message,"WH:",next.warningHigh,0,14) ||
        !(next.low < next.warningLow && next.warningLow < next.warningHigh && next.warningHigh < next.high) ||
        !number(message,"A6:",next.adc686,0,1023) || !number(message,"A9:",next.adc918,0,1023) ||
        !number(message,"FAN:",next.fanThreshold,-54,125) ||
        !integer(message,"CONF:",next.confirmMs,600000) || !integer(message,"SETTLE:",next.settleMs,600000) ||
        !integer(message,"MAINT:",n,1)) return;
    next.maintenance = n;
    if (!integer(message,"ROWS:",n,20) || !n) return;
    next.count = n;
    if ((next.adc686 || next.adc918) &&
        (next.adc686 < 1 || next.adc686 > 1022 || next.adc918 < 1 || next.adc918 > 1022 || next.adc686-next.adc918 < 20)) return;
    pending = next; staging = true;
  } else if (integer(message,"ROW:",revision) && staging && revision == pending.revision) {
    uint32_t index, low, high, category;
    if (!integer(message,"I:",index,19) || index >= pending.count ||
        !integer(message,"MIN:",low,1023) || !integer(message,"MAX:",high,1023) || low>high ||
        !integer(message,"CAT:",category,4)) return;
    pending.ranges[index] = {uint16_t(low), uint16_t(high), uint8_t(category)};
    receivedRows |= (1UL << index);
  } else if (integer(message,"COMMIT:",revision)) {
    if (configured && revision == active.revision) { ack(revision); return; }
    if (!staging || revision != pending.revision || receivedRows != ((1UL << pending.count)-1)) return;
    uint16_t next = 0;
    for (uint8_t i=0;i<pending.count;++i) {
      if (pending.ranges[i].low != next) return;
      next = pending.ranges[i].high+1;
    }
    if (next != 1024) return;
    active = pending; configured = true; staging = false;
    MegaInput::runtimePh686 = active.adc686 ? active.adc686 : MegaInput::PH_ADC_686;
    MegaInput::runtimePh918 = active.adc918 ? active.adc918 : MegaInput::PH_ADC_918;
    MegaInput::runtimePhValid = MegaInput::runtimePh686 > 0 && MegaInput::runtimePh918 > 0 &&
      MegaInput::runtimePh686-MegaInput::runtimePh918 >= 20;
    MegaInput::runtimePhScale = MegaInput::runtimePhValid ? 2.32f/(MegaInput::runtimePh918-MegaInput::runtimePh686) : 0;
    MegaOutput::coolerOnThresholdC = active.fanThreshold;
    MegaOutput::coolerChangePending = false;
    settling = true; settlingClock = false; MegaInput::resetAcquisition();
    ack(revision);
    link.print(F("MegaInputStatus,PHCAL:RUNTIME_RAM,CALIBRATED:"));
    link.println(MegaInput::runtimePhValid ? 1 : 0);
  }
}
uint8_t clarity() {
  if (!configured || !MegaInput::turbidityActive() || !MegaInput::turbidityReady) return 255;
  for (uint8_t i=0;i<active.count;++i)
    if (MegaInput::turbidityRawADC >= active.ranges[i].low && MegaInput::turbidityRawADC <= active.ranges[i].high)
      return active.ranges[i].category;
  return 255;
}
void service(uint32_t now) {
  if (!MegaOutput::systemEnabled) return;
  if (!autoMode) {
    change(MegaOutput::extractionPumpOn,MegaOutput::insertionPumpOn,
      MegaOutput::extractionPumpOn ? (MegaOutput::insertionPumpOn ? "manual_both":"manual_extract") :
      MegaOutput::insertionPumpOn ? "manual_insert":"manual_idle",now);
    return;
  }
  if (!configured) { change(false,false,"idle",now); return; }
  bool upper = MegaInput::floatsReady && MegaInput::sensorEnabled(MegaInput::UPPER_BIT) && MegaInput::upperTriggered;
  bool normal = !upper && MegaInput::floatsReady && MegaInput::sensorEnabled(MegaInput::NORMAL_BIT) && MegaInput::normalTriggered;
  bool veryLow = !upper && !normal && MegaInput::floatsReady && MegaInput::sensorEnabled(MegaInput::LOW_BIT) && !MegaInput::lowTriggered;
  bool low = !upper && !normal && MegaInput::floatsReady && MegaInput::sensorEnabled(MegaInput::LOW_BIT) &&
             MegaInput::sensorEnabled(MegaInput::NORMAL_BIT) && MegaInput::lowTriggered;
  if (settling) {
    if (!normal || MegaOutput::extractionPumpOn || MegaOutput::insertionPumpOn || active.maintenance) settlingClock = false;
    else if (!settlingClock) { settlingClock = true; settlingSince = now; }
    else if (uint32_t(now-settlingSince) >= active.settleMs) { settling = false; settlingClock = false; }
  }
  uint8_t c = active.maintenance ? 255 : clarity();
  float ph = MegaInput::phReadingReady && MegaInput::phActive() && !active.maintenance ?
             MegaInput::calculatePH(MegaInput::phAverageADC) : NAN;
  bool phValid = isfinite(ph) && ph>=0 && ph<=14;
  bool phBad = phValid && (ph < active.warningLow || ph > active.warningHigh);
  uint8_t phCritical = !settling && phValid ? (ph<=active.low ? 1 : ph>=active.high ? 2 : 0) : 0;
  if (quality == 1 && (c == 255 || c>=3)) quality=0;
  if (quality == 2 && !phBad) quality=0;
  if (c == 0) {
    if (!turbCandidate) { turbCandidate=true; turbSince=now; }
    if (uint32_t(now-turbSince)>=active.confirmMs) quality=1;
  } else turbCandidate=false;
  if (phCritical) {
    if (phCandidate != phCritical) { phCandidate=phCritical; phSince=now; }
    if (!quality && uint32_t(now-phSince)>=active.confirmMs) quality=2;
  } else phCandidate=0;
  // Critical-only start: Low warning alone cannot begin an idle refill.
  if (upper) levelCorrection=1;
  else if (veryLow || (low && quality)) levelCorrection=2;
  else if (normal || (low && levelCorrection==1)) levelCorrection=0;
  else if (levelCorrection==1 && !MegaInput::sensorEnabled(MegaInput::NORMAL_BIT) &&
           MegaInput::sensorEnabled(MegaInput::UPPER_BIT) && !MegaInput::upperTriggered) levelCorrection=0;
  if (quality) change(levelCorrection!=2,levelCorrection!=1,
                      quality==1 ? "auto_extract_turbidity":"auto_extract_ph",now);
  else if (levelCorrection==1) {
    if (!strcmp(mode,"auto_insert") && !MegaInput::sensorEnabled(MegaInput::NORMAL_BIT))
      change(false,false,"idle",now);
    else change(true,false,"auto_extract_overflow",now);
  } else if (levelCorrection==2) change(false,true,"auto_insert",now);
  else change(false,false,"idle",now);
}
} // namespace Policy

constexpr uint16_t RX_SIZE = 256;
char rx[RX_SIZE];
uint16_t rxLength = 0;
bool rxOverflow = false;
uint32_t lastIdentityAt = 0;

void inputCommand(const char *text) {
  char command[32];
  strncpy(command, text, sizeof(command)-1); command[sizeof(command)-1] = '\0';
  MegaInput::handleCommand(command);
}
void outputCommand(const char *text) {
  char command[32];
  strncpy(command, text, sizeof(command)-1); command[sizeof(command)-1] = '\0';
  MegaOutput::handleFormattedMessage(command);
}
void stopEverything() {
  outputCommand("MegaOutput,SYSTEM:OFF");
  inputCommand("MegaInput,SYSTEM:OFF");
  Policy::stopped();
}
void identity(bool boot) {
  link.print(boot ? F("AquaPiMega,READY:1") : F("AquaPiMega,STATUS:1"));
  link.print(F(",PROTOCOL:1,FEEDER:V3,FANCTRL:V1,SYSTEMCTRL:V1,SYSTEM:"));
  link.print(MegaOutput::systemEnabled ? F("ON") : F("OFF"));
  link.print(F(",TXDROP:")); link.println(link.dropped);
}
void dispatch(char *message) {
  message = MegaOutput::trimWhitespace(message);
  MegaOutput::toUppercase(message);
  if (!strncmp(message,"MEGACONFIG,",11)) { Policy::configure(message); return; }
  // Exact family prefix: do not treat arbitrary text as a command.
  if (strncmp(message, "MEGAOUTPUT,", 11) && strncmp(message, "MEGAINPUT,", 10)) return;
  char value[28];
  // Either logical role's SYSTEM:OFF always stops the entire physical board.
  if (MegaOutput::copyFieldValue(message, "SYSTEM:", value, sizeof(value))) {
    if (!strcmp(value, "OFF")) { stopEverything(); return; }
    if (!strcmp(value, "ON")) {
      if (!MegaInput::phRawMode) {
        outputCommand("MegaOutput,SYSTEM:ON");
        inputCommand("MegaInput,SYSTEM:ON");
      }
      return;
    }
  }
  if (!strcmp(message, "MEGAINPUT,PHRAW:START")) {
    stopEverything(); MegaInput::handleCommand(message); return;
  }
  if (!strcmp(message, "MEGAINPUT,PHRAW:STOP")) { stopEverything(); return; }
  if (MegaInput::phRawMode) return; // Bench measurement cannot run pumps/feeding.
  if (MegaOutput::copyFieldValue(message, "HEARTBEAT:", value, sizeof(value))) {
    if (!strcmp(value, "1") && MegaOutput::systemEnabled) {
      MegaOutput::lastHostHeartbeat = MegaInput::lastHostHeartbeat = millis();
    }
    return;
  }
  if (MegaOutput::copyFieldValue(message,"WATERMODE:",value,sizeof(value))) {
    if (!strcmp(value,"AUTO")) { Policy::resetQuality(); Policy::autoMode=true; Policy::levelCorrection=0; }
    else if (!strcmp(value,"MANUAL")) Policy::manual();
    Policy::report(millis()); return;
  }
  if (MegaOutput::copyFieldValue(message,"PUMP:",value,sizeof(value))) Policy::manual();
  if (!strncmp(message,"MEGAINPUT,SENSORS:",17)) {
    Policy::resetQuality(); Policy::levelCorrection=0;
  }
  // The Mega owns acquisition modes while automatic control is selected.
  if (Policy::autoMode && !strncmp(message,"MEGAINPUT,MODE:",14)) return;
  if (!strncmp(message, "MEGAINPUT,", 10)) MegaInput::handleCommand(message);
  else MegaOutput::handleFormattedMessage(message);
}
void readCommands() {
  for (uint8_t budget = 0; budget < 32 && Serial.available() > 0; ++budget) {
    char ch = (char)Serial.read();
    if (ch == '\n') {
      if (!rxOverflow && rxLength) { rx[rxLength] = '\0'; dispatch(rx); }
      else if (rxOverflow) link.println(F("AquaPiMega,ERROR:MESSAGE_TOO_LONG"));
      rxLength = 0; rxOverflow = false;
    } else if (ch != '\r' && !rxOverflow) {
      if (rxLength < RX_SIZE-1) rx[rxLength++] = ch;
      else rxOverflow = true;
    }
  }
}
void serviceSafety(uint32_t now) {
  MegaOutput::serviceSystem(now);
  // The output watchdog is the single authority for the combined controller.
  if (!MegaOutput::systemEnabled && MegaInput::systemEnabled) {
    inputCommand("MegaInput,SYSTEM:OFF"); Policy::stopped();
  }
}
void begin() {
  Serial.begin(115200);
  // Identity is the FIRST queued line; Python opens this board only once.
  identity(true);
  MegaOutput::setup(); // Sets pump/fan pins OFF before initializing any sensors.
  MegaInput::setup();
  lastIdentityAt = millis();
}
void tick() {
  serviceSafety(millis()); // Expire before accepting late actuator commands.
  readCommands();
  const uint32_t now = millis();
  serviceSafety(now);
  if (MegaOutput::systemEnabled) {
    MegaOutput::updateFeeder(now);
    MegaOutput::updateCoolerControl(now);
  }
  if (MegaInput::systemEnabled) {
    if (uint32_t(now-MegaInput::lastFloatRead) >= MegaInput::floatInterval) {
      MegaInput::lastFloatRead = now; MegaInput::readWaterFloat();
    }
    MegaInput::serviceAnalog(now);
    bool converting = MegaInput::tempPending;
    MegaInput::serviceTemperature(now);
    if (converting && !MegaInput::tempPending) {
      if (MegaInput::tempValid) MegaOutput::acceptTemperature(MegaInput::waterTempC, now);
      else { MegaOutput::temperatureIsValid=false; MegaOutput::coolerChangePending=false;
             if (MegaOutput::fanMode==MegaOutput::FanMode::AUTO) MegaOutput::writeCoolerRelay(false); }
    }
  } else if (MegaInput::phRawMode) MegaInput::serviceAnalog(now);
  Policy::service(now);
  MegaInput::serviceTransmit(now);
  if (uint32_t(now-MegaOutput::lastCoolerReportAt) >= 2000) {
    MegaOutput::lastCoolerReportAt = now;
    MegaOutput::sendCoolerStatus(F("STATUS"));
  }
  if (uint32_t(now-lastIdentityAt) >= 2000) {
    lastIdentityAt = now; identity(false); Policy::report(now);
  }
  link.drain();
}
} // namespace AquaPi

void setup() { AquaPi::begin(); }
void loop() { AquaPi::tick(); }
