#include <cassert>
#include <cmath>
#include <iostream>
#include "../arduino/AquaPi_Mega/AquaPi_Mega.ino"
using namespace AquaPi;
namespace I = AquaPi::MegaInput;
namespace O = AquaPi::MegaOutput;
void command(const char *s) {
  char buffer[180]; strncpy(buffer, s, 179); buffer[179] = 0; dispatch(buffer);
}
void drain() { for (int i = 0; i < 100; ++i) link.drain(); }
void startBatch() {
  I::resetAcquisition();
  I::serviceAnalog(fakeMillis); // Discard the first ADC conversion after switching.
  assert(I::adcTask == I::AdcTask::TURBIDITY && !I::turbidityReady);
}
void sample(int value) {
  pinInputs[A0] = value;
  fakeMillis += 5;
  I::serviceAnalog(fakeMillis);
}
int main() {
  begin();
  command("MegaInput,SYSTEM:ON");
  command("MegaInput,SENSORS:4,REV:1"); // Only turbidity for exact acquisition checks.
  startBatch();
  int reads = analogReads[A0];
  for (int i = 0; i < 99; ++i) sample(1023);
  assert(!I::turbidityReady && analogReads[A0] - reads == 99);
  I::serviceAnalog(fakeMillis); // Same timestamp must not collect another sample.
  assert(analogReads[A0] - reads == 99);
  sample(1023);
  assert(I::turbidityReady && I::turbidityTotal == 102300);
  assert(I::turbidityRawADC == 1023 && I::turbidityMeanADC == 1023);
  assert(std::string(reinterpret_cast<const char *>(I::turbidityValue)) == "Very Clear");
  assert(std::isnan(I::turbidityNTU));
  drain(); Serial.output.clear();
  for (int i = 0; i < 10; ++i) { I::serviceTransmit(fakeMillis); drain(); }
  assert(Serial.output.find("rawAdc:1023.00,NTU:UNCALIBRATED,SAMPLES:100") != std::string::npos);
  startBatch();
  for (int i = 0; i < 100; ++i) sample(0);
  assert(I::turbidityRawADC == 0 && std::string(reinterpret_cast<const char *>(I::turbidityValue)) == "Very Cloudy");
  startBatch();
  for (int i = 0; i < 100; ++i) sample(i % 2 ? 801 : 800);
  assert(I::turbidityRawADC == 800 && I::turbidityMeanADC == 800.5f);
  // Pausing discards a partially collected batch and any stale serial diagnostic.
  startBatch(); for (int i = 0; i < 50; ++i) sample(0);
  command("MegaInput,MODE:FLOAT_ONLY");
  assert(!I::turbidityReady && !I::turbidityReportPending && I::sampleCount == 0);
  command("MegaInput,MODE:NORMAL");
  I::serviceAnalog(fakeMillis);
  for (int i = 0; i < 100; ++i) sample(900);
  assert(I::turbidityRawADC == 900);

  // Illustrative test fixtures, NOT calibration values to install in a sensor.
  I::TurbidityPoint measured[] = {{900, 0}, {700, 100}, {100, 1000}};
  assert(I::validTurbidityCalibration(measured, 3));
  assert(I::estimateTurbidityNTU(900, measured, 3) == 0);
  assert(I::estimateTurbidityNTU(800, measured, 3) == 50);
  assert(I::estimateTurbidityNTU(700, measured, 3) == 100);
  assert(I::estimateTurbidityNTU(400, measured, 3) == 550);
  assert(I::estimateTurbidityNTU(100, measured, 3) == 1000);
  assert(std::isnan(I::estimateTurbidityNTU(99, measured, 3)));
  assert(std::isnan(I::estimateTurbidityNTU(901, measured, 3)));
  assert(std::isnan(I::estimateTurbidityNTU(NAN, measured, 3)));
  measured[1].adc = 900; // Duplicate/reversed ADC anchors cannot divide by zero.
  assert(!I::validTurbidityCalibration(measured, 3));
  measured[1] = {950, 100}; assert(!I::validTurbidityCalibration(measured, 3));
  measured[1] = {700, -1}; assert(!I::validTurbidityCalibration(measured, 3));
  measured[1] = {700, NAN}; assert(!I::validTurbidityCalibration(measured, 3));

  // Main-loop commands and feeder progress while an ADC batch is underway.
  startBatch();
  command("MegaOutput,FEED:CUSTOM,CYCLES:1,JOB:101");
  for (int i = 0; i < 200; ++i) { ++fakeMillis; tick(); }
  assert(I::sampleCount > 0 && I::sampleCount < 100);
  Serial.input = "MegaOutput,PUMP:BOTH_ON\n";
  for (int i = 0; i < 5; ++i) { ++fakeMillis; tick(); }
  assert(O::extractionPumpOn && O::insertionPumpOn);
  Serial.input = "MegaInput,SYSTEM:OFF\n";
  for (int i = 0; i < 5; ++i) { ++fakeMillis; tick(); }
  assert(!O::extractionPumpOn && !O::insertionPumpOn && !I::turbidityReady);
  assert(!I::turbidityReportPending);
  // millis rollover during the batch still yields exactly 100 conversions.
  command("MegaInput,SYSTEM:ON"); fakeMillis = UINT32_MAX - 200;
  startBatch(); for (int i = 0; i < 100; ++i) sample(900);
  assert(I::turbidityRawADC == 900 && I::turbidityReady);
  std::cout << "Turbidity simulation passed: 100 spaced samples, accumulator range, polarity, NTU validation/interpolation, cancellation, responsive commands and rollover.\n";
}
