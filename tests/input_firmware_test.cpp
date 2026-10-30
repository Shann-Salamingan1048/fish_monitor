// Host simulation of the real sketch: timing, partial UART, calibration and cancellation.
#include <cassert>
#include <cmath>
#include <iostream>
#ifndef AQUAPI_INPUT_SKETCH
#define AQUAPI_INPUT_SKETCH "../arduino/Nano_INPUT_Millis/Nano_INPUT_Millis.ino"
#endif
#include AQUAPI_INPUT_SKETCH
void command(const char* text) {char buf[100];strncpy(buf,text,99);buf[99]='\0';handleCommand(buf);}
void advance(uint32_t milliseconds, bool heartbeat = true) {
 for(uint32_t i=0;i<milliseconds;++i) {
  fakeMillis=uint32_t(fakeMillis+1);
  if(heartbeat && i%1000==0) command("NANOINPUT,HEARTBEAT:1");
  uint32_t before=fakeMillis;loop();
  assert(fakeMillis==before); // No delay waits even while conversions/serial are pending.
 }
}
void packetText() {
 while(packet.busy()) packet.drain();
 Serial.output.clear();sendSerialToRaspberryPi();
 assert(!packet.overflow && packet.length<=256);
 while(packet.busy()) packet.drain();
}
int main() {
 setup();assert(!systemEnabled);advance(100);assert(!temperatureRequests);
 command("NANOINPUT,SYSTEM:ON");assert(!waitForConversion);assert(selectedAnalogReference==DEFAULT);
 assert(Serial.output.find("INPUTFW:ASYNC_V1")!=std::string::npos);
 assert(Serial.output.find("PHCAL:CODE_CONSTANTS")!=std::string::npos);
 assert(Serial.output.find("PH6_ADC:")==std::string::npos);
 pinInputs[A0]=500;pinInputs[A1]=919;
 pinInputs[7]=HIGH;pinInputs[8]=LOW;pinInputs[9]=LOW;
 command("NANOINPUT,SENSORS:0,REV:12");advance(1000);
 assert(!temperatureRequests && !analogReads[A0] && !analogReads[A1]);
 assert(!digitalReads[7] && !digitalReads[8] && !digitalReads[9]);
 packetText();assert(Serial.output.find("Temp:DISABLED")!=std::string::npos);
 assert(Serial.output.find("TurbidityADC:DISABLED")!=std::string::npos);
 assert(Serial.output.find("pH:DISABLED,pHADC:DISABLED")!=std::string::npos);
 command("NANOINPUT,SENSORS:63,REV:13");advance(1);
 packetText();assert(Serial.output.find("TurbidityADC:WAITING")!=std::string::npos);
 assert(tempPending && !tempReady && !phReadingReady);
 advance(300);assert(phReadingReady && phAverageADC==919);
 assert(!tempReady && !temperatureReads);
 advance(120);assert(analogReads[A0]==11 && analogReads[A1]==16);
 assert(turbidityReady && turbidityRawADC==500);
 assert(!tempReady);advance(330);assert(tempReady && tempValid);
 assert(std::fabs(waterTempC-26.8f)<.001f);
 packetText();assert(Serial.output.find("Temp:26.80")!=std::string::npos);
 assert(Serial.output.find("Water Level is Normal")!=std::string::npos);
 assert(Serial.output.find("Turbidity:Slightly Cloudy,TurbidityADC:500")!=std::string::npos);
 if (PH_CALIBRATION_VALID) {
  assert(std::fabs(calculatePH(PH_ADC_686)-6.86f)<.0001f);
  assert(std::fabs(calculatePH(PH_ADC_918)-9.18f)<.0001f);
  assert(std::fabs(calculatePH((PH_ADC_686+PH_ADC_918)/2)-8.02f)<.0001f);
  assert(std::fabs(calculatePH(919)-6.4192f)<.0001f); // Fixture 900/800 only.
  assert(Serial.output.find("pH:6.42,pHADC:919.00")!=std::string::npos);
 } else {
  assert(std::isnan(calculatePH(919)));
  assert(Serial.output.find("pH:WAITING,pHADC:919.00")!=std::string::npos);
 }
 assert(std::isnan(calculatePH(NAN)));
 // A serial configuration command cancels a partial pH batch before publication.
 command("NANOINPUT,SENSORS:63,REV:14");advance(60);
 assert(!phReadingReady && sampleCount>0);
 Serial.input="NANOINPUT,SENSORS:61,REV:15\n";advance(10);
 assert(!sensorEnabled(PH_BIT) && !phReadingReady);
 int phReads=analogReads[A1];advance(500);assert(analogReads[A1]==phReads);
 // Serial TX backpressure never blocks floats or accepting OFF.
 Serial.txSpace=0;advance(600);assert(packet.busy());
 Serial.input="NANOINPUT,SYSTEM:OFF\n";advance(2);assert(!systemEnabled);
 int floatReads=digitalReads[7];advance(500);assert(digitalReads[7]==floatReads);
 Serial.txSpace=7;advance(100);assert(!packet.busy());
 command("NANOINPUT,SYSTEM:ON");command("NANOINPUT,SENSORS:63,REV:16");
 advance(800);assert(tempValid && phReadingReady);
 // Idempotent retries don't continually discard useful conversions.
 command("NANOINPUT,SENSORS:63,REV:16");command("NANOINPUT,MODE:NORMAL");assert(phReadingReady);
 command("NANOINPUT,MODE:FLOAT_ONLY");advance(5);packetText();
 assert(Serial.output.find("TurbidityADC:PAUSED")!=std::string::npos);
 assert(Serial.output.find("pH:PAUSED,pHADC:PAUSED")!=std::string::npos);
 command("NANOINPUT,MODE:NORMAL");probeTemperature=DEVICE_DISCONNECTED_C;
 advance(800);assert(tempReady && !tempValid);packetText();
 assert(Serial.output.find("Temp:ERROR")!=std::string::npos);
 probeTemperature=27;command("NANOINPUT,SENSORS:47,REV:17");advance(1);
 assert(std::string(reinterpret_cast<const char*>(floatValue))=="PARTIAL");
 pinInputs[9]=HIGH;advance(250);packetText();assert(Serial.output.find("Water Level is Very Low")!=std::string::npos);
 command("NANOINPUT,SENSORS:64,REV:18");assert(sensorMask==47 && sensorRevision==17);
 // Overflow line discarded whole; next valid line parsed across bounded RX passes.
 Serial.input=std::string(90,'X')+"\nNANOINPUT,SENSORS:0,REV:18\n";advance(10);assert(sensorMask==0);
 // Timers keep running through the 32-bit millis rollover.
 fakeMillis=UINT32_MAX-100;command("NANOINPUT,SENSORS:63,REV:19");advance(900);
 assert(phReadingReady && tempReady && tempValid);
 // Worst all-disabled/revision packet still fits the bounded TX storage.
 command("NANOINPUT,SENSORS:0,REV:4294967295");advance(1);packetText();
 assert(Serial.output.find("SENSORCFG:4294967295")!=std::string::npos);
 // Each mask/mode must fit the fixed 256-byte buffer, including ADC endpoints.
 for (unsigned mask=0; mask<64; ++mask) {
  char text[80];snprintf(text,sizeof(text),"NANOINPUT,SENSORS:%u,REV:4294967295",mask);
  command(text);
  for (const char* mode : {"NORMAL","AUTO_QUALITY","AUTO_EXTRACT","FLOAT_ONLY"}) {
   snprintf(text,sizeof(text),"NANOINPUT,MODE:%s",mode);command(text);
   packetText();
   pinInputs[A0]=0;advance(800);packetText();
   if(turbidityActive()) assert(Serial.output.find("Turbidity:Very Cloudy,TurbidityADC:0,")!=std::string::npos);
   pinInputs[A0]=1023;advance(2700);packetText();
   if(turbidityActive()) assert(Serial.output.find("Turbidity:Very Clear,TurbidityADC:1023,")!=std::string::npos);
  }
 }
 command("NANOINPUT,SYSTEM:ON");advance(4001,false);assert(!systemEnabled);
 command("NANOINPUT,HEARTBEAT:1");assert(!systemEnabled);
 // Read-only raw ADC measurement works while normal monitoring is off.
 command("NANOINPUT,SYSTEM:OFF");
 command("NANOINPUT,PHRAW:START");assert(phRawMode && !systemEnabled);
 pinInputs[A1]=900;advance(11000,false);
 assert(phRawADC==900 && Serial.output.find("PHRAW:LIVE,pHADC:900.00")!=std::string::npos);
 command("NANOINPUT,SYSTEM:ON");assert(!systemEnabled && phRawMode);
 command("NANOINPUT,SYSTEM:OFF");assert(!phRawMode && !systemEnabled);
 int beforeOff=analogReads[A1];advance(1000,false);assert(analogReads[A1]==beforeOff);
 command("NANOINPUT,PHRAW:START");
 // Trimmed mean discards three low and three high outliers.
 const int samples[15]={0,900,1023,900,100,900,1000,900,200,900,990,900,900,900,900};
 serviceAnalog(fakeMillis);
 for(int value:samples){pinInputs[A1]=value;fakeMillis+=20;serviceAnalog(fakeMillis);}
 assert(phRawADC==900 && phAverageADC==900);
 // EMA alpha .2 converges without blocking and raw diagnostics stay unsmoothed.
 pinInputs[A1]=800;advance(22000,false);
 assert(phRawADC==800 && phAverageADC>800 && phAverageADC<810);
 command("NANOINPUT,PHRAW:STOP");assert(!phRawMode && !systemEnabled);
 std::cout<<"Input firmware timing/protocol/calibration tests passed.\n";
}
