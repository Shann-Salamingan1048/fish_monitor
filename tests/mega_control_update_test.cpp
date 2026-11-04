#include <cassert>
#include <cmath>
#include <iostream>
#include "../arduino/AquaPi_Mega/AquaPi_Mega.ino"
using namespace AquaPi;
namespace P=AquaPi::Policy; namespace I=AquaPi::MegaInput; namespace O=AquaPi::MegaOutput;
void cmd(const char *text) { char b[256]; strncpy(b,text,255); b[255]=0; dispatch(b); }
void level(bool low, bool normal, bool upper) {
  pinInputs[9]=low?LOW:HIGH; pinInputs[8]=normal?LOW:HIGH; pinInputs[7]=upper?LOW:HIGH;
  I::readWaterFloat();
}
void step(uint32_t ms=1) {fakeMillis+=ms; P::service(fakeMillis);}
void config(unsigned rev, unsigned points, const char *acid="1000") {
  std::string base="MegaConfig,BEGIN:"+std::to_string(rev)+",PL:6,PH:9,WL:6.5,WH:8.5,A6:900,A9:800,PC:"+std::to_string(points)+",A4:"+acid+",FAN:35,CONF:100,SETTLE:200,MAINT:0,ROWS:2";
  cmd(base.c_str());
  cmd(("MegaConfig,ROW:"+std::to_string(rev)+",I:0,MIN:0,MAX:170,CAT:0").c_str());
  cmd(("MegaConfig,ROW:"+std::to_string(rev)+",I:1,MIN:171,MAX:1023,CAT:3").c_str());
  cmd(("MegaConfig,COMMIT:"+std::to_string(rev)).c_str());
}
void ready() {
  cmd("MegaOutput,PUMP:STOP_ALL"); cmd("MegaOutput,SYSTEM:ON");
  cmd("MegaOutput,WATERMODE:AUTO"); level(true,true,false);
  I::turbidityReady=false; I::phReadingReady=false;
}
void runFor(uint32_t duration) {
  for(uint32_t i=0;i<duration;++i) {
    ++fakeMillis; if(i%1000==0) cmd("MegaOutput,HEARTBEAT:1"); tick();
  }
}
bool near(float a,float b){return std::fabs(a-b)<0.002f;}
int main() {
  begin(); ready(); config(1,2); assert(P::configured);
  assert(near(I::calculatePH(900),6.86f) && near(I::calculatePH(800),9.18f));
  assert(near(I::calculatePH(1000),4.54f));
  config(2,3); assert(P::configured && I::runtimePhPoints==3);
  assert(near(I::calculatePH(1000),4.01f) && near(I::calculatePH(900),6.86f));
  assert(near(I::calculatePH(950),5.435f) && near(I::calculatePH(850),8.02f));
  assert(near(I::calculatePH(800),9.18f));
  assert(std::isnan(I::calculatePH(NAN)));
  config(3,3,"910"); assert(!P::configured); // Bad span cannot commit.
  config(4,4); assert(!P::configured); // Unknown formula cannot arm.
  config(5,2); assert(P::configured && I::runtimePhPoints==2);
  assert(near(I::calculatePH(1000),4.54f));

  ready(); level(true,false,false); step(); assert(O::insertionPumpOn);
  level(true,true,false); step(); step(2999); assert(O::insertionPumpOn);
  level(true,false,false); step(); // Bounce restarts the full Normal allowance.
  level(true,true,false); step(); step(2999); assert(O::insertionPumpOn);
  step(1); assert(!O::insertionPumpOn);
  level(false,false,false); step(); assert(O::insertionPumpOn);
  level(true,true,false); step(); step(1000);
  level(true,true,true); step(); assert(O::extractionPumpOn && !O::insertionPumpOn);
  level(true,true,false); step(); step(2999); assert(O::extractionPumpOn);
  step(1); assert(!O::extractionPumpOn);
  level(true,true,true); step();
  level(true,true,false); step(); step(1000);
  level(true,false,false); step(); assert(!O::extractionPumpOn && O::insertionPumpOn);
  cmd("MegaOutput,SYSTEM:OFF"); assert(!O::insertionPumpOn && !P::normalClock);
  ready(); fakeMillis=UINT32_MAX-1000;
  level(false,false,false); step(); level(true,true,false); step(); step(3000);
  assert(!O::insertionPumpOn); // Timer rollover is safe.

  ready(); P::settling=false;
  I::phReadingReady=I::turbidityReady=true; I::phAverageADC=950; I::turbidityRawADC=100;
  step(); step(100); assert(P::quality==3 && O::insertionPumpOn && O::extractionPumpOn);
  I::turbidityRawADC=900; step(); assert(P::quality==2 && O::extractionPumpOn);
  I::phAverageADC=850; step(); assert(!P::quality && !O::extractionPumpOn);

  ready(); cmd("MegaOutput,PUMP:BOTH_ON");
  for(int i=0;i<100;++i) link.drain();
  cmd("MegaOutput,FEED:CUSTOM,CYCLES:2,JOB:99");
  assert(O::feederState==O::FeederState::IDLE);
  for(int i=0;i<100;++i) link.drain();
  assert(Serial.output.find("FEED:DEFERRED,JOB:99")!=std::string::npos);
  probeTemperature=36; O::fanMode=O::FanMode::AUTO;
  int before=temperatureReads; runFor(10000);
  assert(temperatureReads>before && I::tempReady && I::tempValid);
  assert(I::sensorMode==I::SensorMode::FLOAT_ONLY && O::coolerIsOn);
  I::packet.beginPacket(); I::sendSerialToRaspberryPi();
  std::string packet((char*)I::packet.data,I::packet.length);
  assert(packet.find("Temp:35.80")!=std::string::npos);
  probeTemperature=34; runFor(10000); assert(!O::coolerIsOn);
  cmd("MegaInput,SENSORS:62,REV:9"); runFor(1000); assert(!I::tempReady);
  cmd("MegaOutput,SYSTEM:OFF"); assert(!O::coolerIsOn && !O::insertionPumpOn);
  std::cout<<"Control update passed: 2/3-point switching, validation, Normal allowance/bounce/limits/rollover, combined quality triggers, temperature and cooler during pumping.\n";
}
