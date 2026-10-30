// Host simulation of the real sketch: timing, partial UART, calibration and cancellation.
#include <cassert>
#include <cmath>
#include <iostream>
#include "../arduino/Nano_INPUT_Millis/Nano_INPUT_Millis.ino"
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
 assert(Serial.output.find("PHCAL:NANO_2POINT_V1")!=std::string::npos);
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
 assert(std::fabs(calculatePH(1006.67f)-4.01f)<.0001f);
 // The midpoint and both halves follow the same line; 6.86 is no longer an anchor.
 assert(std::fabs(calculatePH(923.0f)-6.595f)<.0001f);
 assert(std::fabs(calculatePH(964.835f)-5.3025f)<.0001f);
 assert(std::fabs(calculatePH(881.165f)-7.8875f)<.0001f);
 assert(std::fabs(calculatePH(919.11f)-6.715174f)<.0001f);
 assert(Serial.output.find("pH:6.72,pHADC:919.00")!=std::string::npos);
 assert(std::fabs(calculatePH(839.33f)-9.18f)<.0001f);
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
 std::cout<<"Input firmware timing/protocol/calibration tests passed.\n";
}
