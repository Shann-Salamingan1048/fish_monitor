#pragma once
#define DEVICE_DISCONNECTED_C -127
using DeviceAddress = uint8_t[8];
inline int temperatureRequests = 0, temperatureReads = 0;
inline bool waitForConversion = true, probePresent = true;
inline float probeTemperature = 27.0f;
struct DallasTemperature {
 explicit DallasTemperature(OneWire*) {}
 void begin() {}
 void setWaitForConversion(bool wait) {waitForConversion=wait;}
 bool getAddress(uint8_t* address, int) {address[0]=0x28;return probePresent;}
 void requestTemperatures() { ++temperatureRequests; if(waitForConversion)delay(750); }
 float getTempC(const uint8_t*) {++temperatureReads;return probeTemperature;}
 float getTempCByIndex(int) {++temperatureReads;return probeTemperature;}
};
