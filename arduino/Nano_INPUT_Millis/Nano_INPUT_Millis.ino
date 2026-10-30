#include <Arduino.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <ctype.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>
#include <errno.h>

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

constexpr uint8_t PH_SAMPLE_COUNT = 15;
constexpr uint8_t PH_TRIM_COUNT = 3;    // Average the middle 9; reject 6 outliers.
constexpr float PH_EMA_ALPHA = 0.20f;   // More smoothing = slower response.
static_assert(PH_TRIM_COUNT * 2 < PH_SAMPLE_COUNT, "Invalid pH trimming");
static_assert(PH_EMA_ALPHA > 0.0f && PH_EMA_ALPHA <= 1.0f, "Invalid pH smoothing");
float phRawADC = 0.0f;                 // Trimmed mean, BEFORE exponential smoothing.
bool phRawMode = false;               // Read-only bench diagnostics; never saves calibration.
bool phRawReportPending = false;

void writePhCalibrationStatus(Print &out) {
  out.print(F("NANOINPUTSTATUS,PHCAL:CODE_CONSTANTS,PH_PIN:A1,CALIBRATED:"));
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
uint16_t turbidityTotal = 0;
uint16_t turbidityRawADC = 0; // Average of the same 10 samples used for clarity.
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
      println(F("NANOINPUTSTATUS,ERROR:PACKET_TOO_LONG"));
    }
  }
  void drain() {
    int available = Serial.availableForWrite();
    uint16_t count = length - position;
    if (count > 16) count = 16; // Bound work per loop.
    if (available < count) count = available > 0 ? available : 0;
    if (count) position += Serial.write(data + position, count);
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
  phRawADC = phAverageADC = 0.0f;
  phRawReportPending = false;
  floatValue = turbidityValue = F("WAITING");
  tempPending = false; adcTask = AdcTask::IDLE; sampleCount = 0;
  lastTempRead = now - tempInterval;
  lastPhRead = now - phInterval;
  lastTurbidityRead = now - turbidityInterval;
  lastFloatRead = now - floatInterval;
}

constexpr size_t COMMAND_BUFFER_SIZE = 64;
char commandBuffer[COMMAND_BUFFER_SIZE];
uint8_t commandLength = 0;
bool commandOverflow = false;
void writeModeStatus(Print &out) {
  out.print(F("NANOINPUTSTATUS,MODE:"));
  out.println(sensorMode == SensorMode::NORMAL ? F("NORMAL") :
    sensorMode == SensorMode::AUTO_QUALITY ? F("AUTO_QUALITY") :
    sensorMode == SensorMode::AUTO_EXTRACT ? F("AUTO_EXTRACT") : F("FLOAT_ONLY"));
}
void handleCommand(char *message) {
  // Commands are ASCII; normalize only text, not numeric conversion semantics.
  for (char *p = message; *p; ++p) *p = toupper((unsigned char)*p);

  // Optional read-only pH ADC view for measuring your buffers with main.py stopped.
  // There are no capture/save commands and no EEPROM reads or writes.
  if (strcmp_P(message, PSTR("NANOINPUT,PHRAW:START")) == 0) {
    systemEnabled = false;
    phRawMode = true;
    resetAcquisition();
    return;
  }
  if (strcmp_P(message, PSTR("NANOINPUT,PHRAW:STOP")) == 0 ||
      strcmp_P(message, PSTR("NANOINPUT,SYSTEM:OFF")) == 0) {
    systemEnabled = phRawMode = false;
    resetAcquisition();
    systemStatusPending = true;
    return;
  }
  // A Pi auto-arm must not interrupt a deliberate bench measurement session.
  if (phRawMode) return;

  if (strcmp_P(message, PSTR("NANOINPUT,SYSTEM:OFF")) == 0 ||
      strcmp_P(message, PSTR("NANOINPUT,SYSTEM:ON")) == 0) {
    bool requested = strcmp_P(message, PSTR("NANOINPUT,SYSTEM:ON")) == 0;
    if (requested != systemEnabled) { systemEnabled = requested; resetAcquisition(); }
    if (requested) lastHostHeartbeat = millis();
    systemStatusPending = true;
    return;
  }
  if (strcmp_P(message, PSTR("NANOINPUT,HEARTBEAT:1")) == 0) {
    if (systemEnabled) lastHostHeartbeat = millis();
    return;
  }
  if ((strncmp_P(message, PSTR("NANOINPUT,SENSORS:"), 17) == 0)) {
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
  if (!(strncmp_P(message, PSTR("NANOINPUT,MODE:"), 14) == 0)) return;
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
void readSerialCommands() {
  // Even continuous garbage input cannot starve floats or temperature service.
  for (uint8_t budget = 0; budget < 32 && Serial.available() > 0; ++budget) {
    char ch = Serial.read();
    if (ch == '\n') {
      if (!commandOverflow && commandLength) {
        commandBuffer[commandLength] = '\0'; handleCommand(commandBuffer);
      }
      commandLength = 0; commandOverflow = false;
    } else if (ch != '\r' && !commandOverflow) {
      if (commandLength < sizeof(commandBuffer)-1) commandBuffer[commandLength++] = ch;
      else commandOverflow = true;
    }
  }
}

float calculatePH(float adc) {
  if (!PH_CALIBRATION_VALID || !isfinite(adc)) return NAN;
  // Anchored form passes through both measured points without rounding ADC first.
  return PH_BUFFER_686 + (adc - PH_ADC_686) * PH_PER_ADC;
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
  if (uint32_t(now-lastAdcSample) < (adcTask == AdcTask::PH ? 20U : 10U)) return;
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
    if (++sampleCount == 10) {
      const uint16_t raw = turbidityTotal / 10;
      turbidityRawADC = raw;
      turbidityReady = true;
      const float clarity = (raw / 1023.0f) * 3000.0f;
      turbidityValue = clarity < 500 ? F("Very Cloudy") : clarity < 1200 ? F("Cloudy") :
        clarity < 2200 ? F("Slightly Cloudy") : clarity < 2800 ? F("Clear") : F("Very Clear");
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
  packet.print(F("NANOINPUT,Temp:"));
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
  else if (!phReadingReady || !PH_CALIBRATION_VALID) packet.print(F("WAITING"));
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
      packet.print(F("NANOINPUTSTATUS,PHRAW:LIVE,pHADC:"));
      packet.print(phRawADC, 2);
      packet.print(F(",pH:"));
      if (PH_CALIBRATION_VALID) packet.println(calculatePH(phAverageADC), 2);
      else packet.println(F("WAITING"));
    } else if (systemStatusPending ||
               (!systemEnabled && !phRawMode && uint32_t(now-lastSerialSend) >= 2000)) {
      systemStatusPending = false; lastSerialSend = now;
      packet.print(F("NANOINPUTSTATUS,SYSTEM:")); packet.println(systemEnabled ? F("ON") : F("OFF"));
    } else if (configStatusPending) {
      configStatusPending = false;
      packet.print(F("NANOINPUTSTATUS,SENSORCFG:")); packet.println(sensorRevision);
    } else if (modeStatusPending) {
      modeStatusPending = false; writeModeStatus(packet);
    } else if (systemEnabled && uint32_t(now-lastSerialSend) >= serialInterval) {
      lastSerialSend = now; sendSerialToRaspberryPi();
    }
  }
  packet.drain();
}
void setup() {
  Serial.begin(115200);
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
  Serial.println(F("NANOINPUTSTATUS,READY:1,INPUTFW:ASYNC_V1,SYSTEMCTRL:V1,SYSTEM:OFF"));
  writeModeStatus(Serial);
  writePhCalibrationStatus(Serial);
}
void loop() {
  if (systemEnabled && !phRawMode &&
      uint32_t(millis()-lastHostHeartbeat) >= HOST_TIMEOUT_MS) {
    systemEnabled = false;
    resetAcquisition();
    systemStatusPending = true;
  }
  readSerialCommands();
  const uint32_t now = millis();
  if (systemEnabled) {
    if (uint32_t(now-lastFloatRead) >= floatInterval) {
      lastFloatRead = now; readWaterFloat();
    }
    serviceAnalog(now);
    serviceTemperature(now);
  } else if (phRawMode) {
    // Read-only pH measurements: non-blocking, no heartbeat required.
    serviceAnalog(now);
  }
  serviceTransmit(now);
}
