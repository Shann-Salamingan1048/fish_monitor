#include <Arduino.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <ctype.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>
#include <errno.h>
#include <EEPROM.h>
#include <stddef.h>

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

// Genuine two-point calibration uses measured ADC readings at pH 6.86 and 9.18.
// ADC values MUST be captured from this individual sensor, not estimated from
// the old pH 4.01 / 9.18 calibration. Saved EEPROM data is loaded at boot.
constexpr float PH_BUFFER_686 = 6.86f;
constexpr float PH_BUFFER_918 = 9.18f;
constexpr uint8_t PH_SAMPLE_COUNT = 15;
constexpr uint8_t PH_TRIM_COUNT = 3;     // Discard 3 lowest and 3 highest.
constexpr float PH_EMA_ALPHA = 0.20f;  // Smoothing only, not calibration.
constexpr uint8_t PH_STABLE_FRAMES = 5; // Five readings ~2 seconds apart.
constexpr float PH_STABLE_MAX_SPREAD_ADC = 4.0f;
static_assert(PH_TRIM_COUNT * 2 < PH_SAMPLE_COUNT, "Invalid pH trimming");

// 14-byte record on AVR Nano; checksum detects corrupted/incomplete data.
constexpr uint32_t PH_CAL_MAGIC = 0x50483639UL;
struct PhCalibrationData {
  uint32_t magic;
  float adc686;
  float adc918;
  uint16_t crc;
};

float phCalAdc686 = 0.0f, phCalAdc918 = 0.0f;
bool phCalibrated = false;
bool phCalibrationMode = false;
bool phCalibrationComplete = false;
bool phCapture686 = false, phCapture918 = false;
float phPending686 = 0.0f, phPending918 = 0.0f;
float phRawADC = 0.0f;               // Raw trimmed-mean; sent as pHADC.
float phHistory[PH_STABLE_FRAMES] = {};
uint8_t phHistoryCount = 0, phHistoryNext = 0;
bool phCalReportPending = false, phCalStatusPending = false;

enum class PhCalEvent : uint8_t {
  NONE, STARTED, STOPPED, NOT_IN_MODE, NOT_STABLE,
  CAPTURED_686, CAPTURED_918, SAVED, INVALID, ALREADY_SAVED
};
PhCalEvent phCalEvent = PhCalEvent::NONE;

uint16_t phCalibrationCrc(const PhCalibrationData &record) {
  uint16_t crc = 0xFFFF;
  const uint8_t *bytes = reinterpret_cast<const uint8_t *>(&record);
  for (size_t i = 0; i < offsetof(PhCalibrationData, crc); ++i) {
    crc ^= uint16_t(bytes[i]) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000U) ? uint16_t((crc << 1) ^ 0x1021U) : uint16_t(crc << 1);
    }
  }
  return crc;
}

bool validPhCalibration(float adc686, float adc918) {
  // This interface's ADC falls as pH rises (as observed with the prior buffers).
  return isfinite(adc686) && isfinite(adc918) &&
         adc686 > 0.0f && adc686 < 1023.0f &&
         adc918 > 0.0f && adc918 < 1023.0f &&
         (adc686 - adc918) >= 20.0f;
}

void loadPhCalibration() {
  PhCalibrationData record = {};
  EEPROM.get(0, record);
  if (record.magic == PH_CAL_MAGIC &&
      record.crc == phCalibrationCrc(record) &&
      validPhCalibration(record.adc686, record.adc918)) {
    phCalAdc686 = record.adc686;
    phCalAdc918 = record.adc918;
    phCalibrated = true;
  }
}

void savePhCalibration(float adc686, float adc918) {
  PhCalibrationData record = {};
  record.magic = PH_CAL_MAGIC;
  record.adc686 = adc686;
  record.adc918 = adc918;
  record.crc = phCalibrationCrc(record);
  EEPROM.put(0, record);  // Arduino EEPROM.put() updates changed bytes only.
  phCalAdc686 = adc686;
  phCalAdc918 = adc918;
  phCalibrated = true;
}

void rememberPhRawADC(float adc) {
  phHistory[phHistoryNext] = adc;
  phHistoryNext = (phHistoryNext + 1) % PH_STABLE_FRAMES;
  if (phHistoryCount < PH_STABLE_FRAMES) ++phHistoryCount;
}

bool phRawIsStable() {
  if (phHistoryCount < PH_STABLE_FRAMES) return false;
  float lowest = phHistory[0], highest = phHistory[0];
  for (uint8_t i = 1; i < PH_STABLE_FRAMES; ++i) {
    if (phHistory[i] < lowest) lowest = phHistory[i];
    if (phHistory[i] > highest) highest = phHistory[i];
  }
  return (highest - lowest) <= PH_STABLE_MAX_SPREAD_ADC;
}

float phStableAdcAverage() {
  float sum = 0.0f;
  for (uint8_t i = 0; i < PH_STABLE_FRAMES; ++i) sum += phHistory[i];
  return sum / PH_STABLE_FRAMES;
}

void capturePhCalibration(bool buffer686) {
  if (!phCalibrationMode) { phCalEvent = PhCalEvent::NOT_IN_MODE; return; }
  if (phCalibrationComplete) { phCalEvent = PhCalEvent::ALREADY_SAVED; return; }
  if (!phRawIsStable()) { phCalEvent = PhCalEvent::NOT_STABLE; return; }

  const float adc = phStableAdcAverage();
  // Each capture needs a fresh stability window, even when retrying a buffer.
  phHistoryCount = phHistoryNext = 0;
  if (buffer686) {
    phPending686 = adc;
    phCapture686 = true;
    phCalEvent = PhCalEvent::CAPTURED_686;
  } else {
    phPending918 = adc;
    phCapture918 = true;
    phCalEvent = PhCalEvent::CAPTURED_918;
  }
  if (phCapture686 && phCapture918) {
    if (!validPhCalibration(phPending686, phPending918)) {
      phCalEvent = PhCalEvent::INVALID;
      return;  // The user may repeat either measurement.
    }
    savePhCalibration(phPending686, phPending918);
    phCalibrationComplete = true;
    phCalEvent = PhCalEvent::SAVED;
  }
}

void writePhCalibrationStatus(Print &out) {
  out.print(F("NANOINPUTSTATUS,PHCAL:NANO_2POINT_V2,PH_PIN:A1,CALIBRATED:"));
  out.print(phCalibrated ? 1 : 0);
  if (phCalibrated) {
    out.print(F(",PH686_ADC:")); out.print(phCalAdc686, 2);
    out.print(F(",PH918_ADC:")); out.print(phCalAdc918, 2);
  }
  out.println();
}

void writePhCalEvent(Print &out) {
  out.print(F("NANOINPUTSTATUS,PHCAL:"));
  switch (phCalEvent) {
    case PhCalEvent::STARTED:       out.print(F("STARTED")); break;
    case PhCalEvent::STOPPED:       out.print(F("STOPPED")); break;
    case PhCalEvent::NOT_IN_MODE:   out.print(F("ERROR:START_REQUIRED")); break;
    case PhCalEvent::NOT_STABLE:    out.print(F("ERROR:WAIT_FOR_STABLE_ADC")); break;
    case PhCalEvent::CAPTURED_686:  out.print(F("CAPTURED_6.86,ADC:")); out.print(phPending686, 2); break;
    case PhCalEvent::CAPTURED_918:  out.print(F("CAPTURED_9.18,ADC:")); out.print(phPending918, 2); break;
    case PhCalEvent::SAVED:
      out.print(F("SAVED,PH686_ADC:")); out.print(phCalAdc686, 2);
      out.print(F(",PH918_ADC:")); out.print(phCalAdc918, 2);
      break;
    case PhCalEvent::INVALID:      out.print(F("ERROR:INVALID_ADC_PAIR")); break;
    case PhCalEvent::ALREADY_SAVED: out.print(F("ERROR:STOP_OR_RESTART_CALIBRATION")); break;
    case PhCalEvent::NONE:         out.print(F("NONE")); break;
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
  return phCalibrationMode || (sensorEnabled(PH_BIT) &&
    (sensorMode == SensorMode::NORMAL || sensorMode == SensorMode::AUTO_QUALITY));
}
bool turbidityActive() {
  return !phCalibrationMode && sensorEnabled(TURBIDITY_BIT) &&
         sensorMode != SensorMode::FLOAT_ONLY;
}
void resetAcquisition() {
  const uint32_t now = millis();
  tempReady = tempValid = phReadingReady = floatsReady = false;
  turbidityReady = false;
  phRawADC = phAverageADC = 0.0f;
  phHistoryCount = phHistoryNext = 0;
  phCalReportPending = false;
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

  // Explicit system OFF must stop acquisition, including calibration mode.
  if (strcmp_P(message, PSTR("NANOINPUT,SYSTEM:OFF")) == 0) {
    systemEnabled = false;
    if (phCalibrationMode) phCalEvent = PhCalEvent::STOPPED;
    phCalibrationMode = false;
    resetAcquisition();
    systemStatusPending = true;
    return;
  }

  // Calibration can run with the normal system OFF: no Pi heartbeat is needed.
  if (strcmp_P(message, PSTR("NANOINPUT,PHCAL:START")) == 0) {
    systemEnabled = false;
    phCalibrationMode = true;
    phCalibrationComplete = phCapture686 = phCapture918 = false;
    phPending686 = phPending918 = 0.0f;
    resetAcquisition();
    phCalEvent = PhCalEvent::STARTED;
    return;
  }
  if (strcmp_P(message, PSTR("NANOINPUT,PHCAL:STOP")) == 0) {
    phCalibrationMode = false;
    resetAcquisition();
    phCalEvent = PhCalEvent::STOPPED;
    return;
  }
  if (strcmp_P(message, PSTR("NANOINPUT,PHCAL:STATUS")) == 0) {
    phCalStatusPending = true;
    return;
  }
  if (strcmp_P(message, PSTR("NANOINPUT,PHCAL:6.86")) == 0) {
    capturePhCalibration(true);
    return;
  }
  if (strcmp_P(message, PSTR("NANOINPUT,PHCAL:9.18")) == 0) {
    capturePhCalibration(false);
    return;
  }
  // Ignore external system/mode/sensor commands during deliberate calibration.
  if (phCalibrationMode) return;

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
  // Only call when phCalibrated == true; no guessed neutral-buffer ADC.
  // pH 6.86 and 9.18 use freshly measured ADC data saved in EEPROM.
  return PH_BUFFER_686 + (adc - phCalAdc686) *
         (PH_BUFFER_918 - PH_BUFFER_686) / (phCalAdc918 - phCalAdc686);
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
      rememberPhRawADC(phRawADC);
      phReadingReady = true;
      if (phCalibrationMode) phCalReportPending = true;
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
  else if (!phReadingReady || !phCalibrated) packet.print(F("WAITING"));
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
    if (phCalEvent != PhCalEvent::NONE) {
      writePhCalEvent(packet);
      phCalEvent = PhCalEvent::NONE;
    } else if (phCalStatusPending) {
      phCalStatusPending = false;
      writePhCalibrationStatus(packet);
    } else if (phCalibrationMode && phCalReportPending) {
      phCalReportPending = false;
      packet.print(F("NANOINPUTSTATUS,PHCAL:LIVE,ADC:"));
      packet.print(phRawADC, 2);
      packet.print(F(",STABLE:"));
      packet.println(phRawIsStable() ? 1 : 0);
    } else if (systemStatusPending ||
               (!systemEnabled && !phCalibrationMode && uint32_t(now-lastSerialSend) >= 2000)) {
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
  loadPhCalibration();
  resetAcquisition();
  // Boot diagnostics only; runtime output is buffered and drained without waits.
  Serial.println(F("NANOINPUTSTATUS,READY:1,INPUTFW:ASYNC_V1,SYSTEMCTRL:V1,SYSTEM:OFF"));
  writeModeStatus(Serial);
  writePhCalibrationStatus(Serial);
}
void loop() {
  if (systemEnabled && !phCalibrationMode &&
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
  } else if (phCalibrationMode) {
    // Deliberate pH-only calibration: non-blocking, no heartbeat required.
    serviceAnalog(now);
  }
  serviceTransmit(now);
}
