#include <cassert>
#include <iostream>
#include "../arduino/AquaPi_Mega/AquaPi_Mega.ino"
using namespace AquaPi;
namespace O = AquaPi::MegaOutput;
namespace I = AquaPi::MegaInput;
void command(const char *text) {
 char buffer[180]; strncpy(buffer,text,179);buffer[179]=0;dispatch(buffer);
}
void drain() { for(int i=0;i<100;++i) link.drain(); }
void runFor(uint32_t duration) {
 for(uint32_t i=0;i<duration;++i) {
  ++fakeMillis;
  if (i%1000==0) command("MegaOutput,HEARTBEAT:1");
  tick();
 }
}
int main() {
 begin(); drain();
 assert(Serial.output.rfind("AquaPiMega,READY:1",0)==0);
 assert(!O::systemEnabled && !I::systemEnabled);
 assert(pinOutputs[22]==LOW && pinOutputs[23]==LOW);
 command("MegaOutput,PUMP:BOTH_ON");assert(!O::extractionPumpOn);
 command("MegaInput,SYSTEM:ON");assert(O::systemEnabled && I::systemEnabled);
 command("MegaOutput,PUMP:BOTH_ON");assert(O::extractionPumpOn && O::insertionPumpOn);
 assert(pinOutputs[22]==HIGH && pinOutputs[23]==HIGH);
 command("MegaOutput,PUMP:EXTRACT_ON");assert(O::extractionPumpOn && !O::insertionPumpOn);
 command("MegaOutput,PUMP:INSERT_ON");assert(!O::extractionPumpOn && O::insertionPumpOn);
 command("MegaOutput,PUMP:STOP_ALL");
 pinInputs[A1]=800;pinInputs[A0]=900;
 pinInputs[7]=HIGH;pinInputs[8]=LOW;pinInputs[9]=LOW;
 command("MegaOutput,FEED:CUSTOM,CYCLES:3,JOB:99");
 drain();Serial.output.clear();
 runFor(6500);drain();
 assert(O::feederState==O::FeederState::IDLE);
 assert(Serial.output.find("FEED:COMPLETE,JOB:99")!=std::string::npos);
 assert(Serial.output.find("MegaInput,Temp:")!=std::string::npos);
 assert(Serial.output.find("TurbidityADC:900")!=std::string::npos);
 assert(I::phReadingReady && I::phRawADC==800 && I::phAverageADC==800);
 assert(O::systemEnabled && I::systemEnabled); // Output heartbeat keeps BOTH alive.
 // Every output line is whole even while feeder/status and telemetry coexist.
 std::istringstream lines(Serial.output);std::string line;
 while(std::getline(lines,line)) {
   assert(line.rfind("MegaInput,",0)==0 || line.rfind("MegaInputStatus,",0)==0 ||
          line.rfind("MegaOutput,",0)==0 || line.rfind("MegaServo,",0)==0 || line.rfind("AquaPiMega,",0)==0);
   assert(line.find("MegaInput",1)==std::string::npos && line.find("MegaOutput",1)==std::string::npos);
 }
 command("MegaInput,SENSORS:62,REV:8"); assert(!I::sensorEnabled(I::TEMP_BIT));
 command("MegaInput,MODE:FLOAT_ONLY");assert(I::sensorMode==I::SensorMode::FLOAT_ONLY);
 command("MegaOutput,PUMP:BOTH_ON");command("MegaOutput,FANMODE:ON");
 command("MegaInput,SYSTEM:OFF");
 assert(!O::systemEnabled && !I::systemEnabled && !O::coolerIsOn && !O::extractionPumpOn && !O::insertionPumpOn);
 fakeMillis+=1000;tick();assert(O::pwm.channelPulse[2]==4096);
 command("MegaInput,PHRAW:START");command("MegaOutput,SYSTEM:ON");command("MegaOutput,PUMP:BOTH_ON");
 assert(I::phRawMode && !O::systemEnabled && !O::extractionPumpOn);
 command("MegaInput,PHRAW:STOP");assert(!I::phRawMode);
 command("MegaOutput,SYSTEM:ON");command("MegaOutput,PUMP:BOTH_ON");
 fakeMillis+=4000;tick();assert(!O::systemEnabled && !I::systemEnabled && !O::extractionPumpOn);
 // UART backpressure never prevents the watchdog from stopping pumps.
 command("MegaOutput,SYSTEM:ON");command("MegaOutput,PUMP:BOTH_ON");
 Serial.txSpace=0;
 for(int i=0;i<100;++i) O::sendPumpStatus(F("TEST"));
 assert(link.dropped>0);
 fakeMillis+=4000;tick();assert(!O::systemEnabled && !I::systemEnabled && !O::insertionPumpOn);
 Serial.txSpace=64;drain();
 // Oversized commands are discarded without executing their final suffix.
 Serial.input=std::string(181,'X')+"MegaOutput,SYSTEM:ON\n";
 for(int i=0;i<10;++i)tick();assert(!O::systemEnabled);
 // Rollover-safe shared heartbeat.
 fakeMillis=UINT32_MAX-100;command("MegaOutput,SYSTEM:ON");
 fakeMillis=uint32_t(fakeMillis+4000);tick();assert(!O::systemEnabled && !I::systemEnabled);
 std::cout << "Mega simulation passed: simultaneous services, shared heartbeat, both pumps, feed cycles, complete frames, backpressure, all-off and overflow.\n";
}
