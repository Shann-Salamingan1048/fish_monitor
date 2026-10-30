#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>
#include <ctype.h>
#include <math.h>
#include <stdlib.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>

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
// IN1 -> D8 = extraction pump
// IN3 -> D9 = insertion/fresh-water pump
// IN2 and IN4 must remain at logic LOW for this direction mapping.
constexpr uint8_t EXTRACTION_PUMP_PIN = 8;
constexpr uint8_t INSERTION_PUMP_PIN = 9;
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
  Serial.print(F("NANOOUTPUT,PUMP_EXTRACT:"));
  Serial.print(extractionPumpOn ? F("ON") : F("OFF"));
  Serial.print(F(",PUMP_INSERT:"));
  Serial.print(insertionPumpOn ? F("ON") : F("OFF"));
  Serial.print(F(",REASON:"));
  Serial.println(reason);
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
  Serial.print(F("NANOSERVO,TILT:"));
  Serial.print(tiltAngle);
  Serial.print(F(",PAN:"));
  Serial.println(panAngle);
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
  Serial.print(F("NANOOUTPUT,COOLER:"));
  Serial.print(coolerIsOn ? F("ON") : F("OFF"));
  Serial.print(F(",FANCTRL:V1,FANMODE:"));
  Serial.print(fanMode == FanMode::AUTO ? F("AUTO") : fanMode == FanMode::ON ? F("ON") : F("OFF"));
  Serial.print(F(",FANPENDING:"));
  Serial.print(coolerChangePending ? (coolerPendingState ? F("ON") : F("OFF")) : F("NONE"));
  Serial.print(F(",TEMP:"));

  if (temperatureIsValid) Serial.print(latestTemperatureC, 2);
  else Serial.print(F("ERROR"));

  Serial.print(F(",THRESHOLD:"));
  Serial.print(coolerOnThresholdC, 2);
  Serial.print(F(",REASON:"));
  Serial.println(reason);
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
    Serial.println(F("NANOOUTPUT,ERROR:INVALID_TEMPERATURE"));
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
  Serial.print(F("NANOOUTPUT,FEED:"));
  Serial.print(event);
  Serial.print(F(",JOB:"));
  Serial.println(job);
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
  Serial.print(F("NANOOUTPUT,SYSTEM:OFF,SYSTEMCTRL:V1,REASON:"));
  Serial.println(reason);
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
  if (!startsWithIgnoreCase(message, "NANOOUTPUT")) return;

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
      Serial.println(F("NANOOUTPUT,SYSTEM:ON,SYSTEMCTRL:V1"));
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
      Serial.print(F("NANOOUTPUT,TEMPSENSOR:"));
      Serial.println(temperatureSensorEnabled ? F("ON") : F("OFF"));
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

// ============================== Serial reader ==============================

constexpr size_t SERIAL_MESSAGE_SIZE = 180;
char serialMessage[SERIAL_MESSAGE_SIZE];
size_t serialMessageLength = 0;
bool serialMessageOverflow = false;

void readSerialMessages()
{
  for (uint8_t budget = 0; budget < 32 && Serial.available() > 0; ++budget) {
    const char incoming = (char)Serial.read();

    if (incoming == '\n') {
      if (!serialMessageOverflow && serialMessageLength > 0) {
        serialMessage[serialMessageLength] = '\0';
        handleFormattedMessage(serialMessage);
      }
      else if (serialMessageOverflow) {
        Serial.println(F("NANOOUTPUT,ERROR:MESSAGE_TOO_LONG"));
      }

      serialMessageLength = 0;
      serialMessageOverflow = false;
      continue;
    }

    if (incoming == '\r') continue;

    if (!serialMessageOverflow) {
      if (serialMessageLength < SERIAL_MESSAGE_SIZE - 1) {
        serialMessage[serialMessageLength++] = incoming;
      }
      else {
        serialMessageOverflow = true;
      }
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

  Serial.begin(115200);
  Wire.begin();

  pwm.begin();
  pwm.setPWMFreq(50);
  delay(10);

  // PCA9685 can stay powered through a Nano reset; explicitly clear PWM.
  pwm.setPWM(PAN_CHANNEL, 0, 4096);
  pwm.setPWM(TILT_CHANNEL, 0, 4096);
  pwm.setPWM(FEEDER_CHANNEL, 0, 4096);

  Serial.println(F("NANOOUTPUT,READY:1,FEEDER:V3,FANCTRL:V1,SYSTEMCTRL:V1,SYSTEM:OFF"));
  sendServoPosition();
  sendCoolerStatus(F("WAITING_FOR_TEMPERATURE"));
  sendPumpStatus(F("READY"));
}

void loop()
{
  serviceSystem(millis()); // Expire before processing a late actuator command.
  readSerialMessages();

  const unsigned long now = millis();
  serviceSystem(now);
  if (systemEnabled) { updateFeeder(now); updateCoolerControl(now); }
  if (now - lastCoolerReportAt >= 2000UL) {
    lastCoolerReportAt = now;
    sendCoolerStatus(F("STATUS"));
  }
}
