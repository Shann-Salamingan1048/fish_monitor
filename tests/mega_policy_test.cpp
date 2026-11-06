#include <cassert>
#include <iostream>
#include "../arduino/AquaPi_Mega/AquaPi_Mega.ino"
using namespace AquaPi;
namespace P=AquaPi::Policy;namespace I=AquaPi::MegaInput;namespace O=AquaPi::MegaOutput;
void cmd(const char* text) {char b[256];strncpy(b,text,255);b[255]=0;dispatch(b);}
void normal(){I::floatsReady=true;I::upperTriggered=false;I::normalTriggered=true;I::lowTriggered=true;}
void readings(int turb,float adc){I::turbidityReady=true;I::turbidityRawADC=turb;I::phReadingReady=true;I::phAverageADC=adc;}
void step(uint32_t ms=1){fakeMillis+=ms;P::service(fakeMillis);}
int main(){
 begin();cmd("MegaOutput,SYSTEM:ON");cmd("MegaOutput,WATERMODE:AUTO");
 cmd("MegaConfig,BEGIN:1,PL:6,PH:9,WL:6.5,WH:8.5,A6:900,A9:800,FAN:35,CONF:100,SETTLE:200,MAINT:0,ROWS:5");
 cmd("MegaConfig,ROW:1,I:0,MIN:0,MAX:170,CAT:0");
 cmd("MegaConfig,ROW:1,I:1,MIN:171,MAX:409,CAT:1");
 cmd("MegaConfig,ROW:1,I:2,MIN:410,MAX:750,CAT:2");
 cmd("MegaConfig,ROW:1,I:3,MIN:751,MAX:954,CAT:3");
 cmd("MegaConfig,COMMIT:1");assert(!P::configured); // Missing row cannot arm rules.
 cmd("MegaConfig,ROW:1,I:4,MIN:955,MAX:1023,CAT:4");cmd("MegaConfig,COMMIT:1");assert(P::configured);
 assert(I::calculatePH(900)>6.85f && I::calculatePH(800)<9.19f);
 // Test initial triggering independently from a previously latched correction.
 for (int adc : {100, 300, 600, 900, 1000}) {
   cmd("MegaOutput,PUMP:STOP_ALL"); cmd("MegaOutput,WATERMODE:AUTO");
   normal(); readings(adc,850); P::settling=false;
   step(); step(99); assert(!O::extractionPumpOn && !O::insertionPumpOn);
   step(1);
   const bool shouldStart = adc <= 409;
   assert(O::extractionPumpOn == shouldStart && O::insertionPumpOn == shouldStart);
 }
 for (int phADC : {928, 817, 950, 790}) {
   cmd("MegaOutput,PUMP:STOP_ALL"); cmd("MegaOutput,WATERMODE:AUTO");
   normal(); readings(900,phADC); P::settling=false;
   const float ph = I::calculatePH(phADC);
   const bool critical = ph <= P::active.low || ph >= P::active.high;
   assert(critical == (phADC == 950 || phADC == 790));
   step(); step(99); assert(!O::extractionPumpOn);
   step(1);
   assert(O::extractionPumpOn == critical && O::insertionPumpOn == critical);
 }
 // Warning interrupts an unconfirmed Critical pH candidate.
 cmd("MegaOutput,PUMP:STOP_ALL"); cmd("MegaOutput,WATERMODE:AUTO");
 normal(); readings(900,950); P::settling=false; step(); step(99);
 readings(900,928); step(); step(100); assert(!O::extractionPumpOn);
 // A brief Cloudy reading must also pass the full confirmation interval.
 readings(300,850); step(); step(99);
 readings(600,850); step(); step(100); assert(!O::extractionPumpOn);
 readings(300,850); step(); step(99); assert(!O::extractionPumpOn);
 step(1); assert(O::extractionPumpOn && O::insertionPumpOn);
 // Recovery stays latched through Slightly Cloudy and ends at Clear.
 readings(600,850); step(); assert(O::extractionPumpOn && O::insertionPumpOn);
 readings(900,850); step(); assert(!O::extractionPumpOn && !O::insertionPumpOn);
 cmd("MegaOutput,PUMP:STOP_ALL"); cmd("MegaOutput,WATERMODE:AUTO");

 normal();readings(600,850);step();step(200);
 assert(!O::extractionPumpOn && !O::insertionPumpOn); // Slightly Cloudy alone does not start correction.
 readings(100,850);step();step(99);assert(!O::extractionPumpOn);
 step(1);assert(O::extractionPumpOn && O::insertionPumpOn);
 step();assert(O::extractionPumpOn && O::insertionPumpOn); // No mode-change oscillation.
 I::upperTriggered=true;step();assert(O::extractionPumpOn && !O::insertionPumpOn);
 normal();step();step(3000);assert(O::extractionPumpOn && O::insertionPumpOn);
 I::normalTriggered=false;step();assert(!O::extractionPumpOn && O::insertionPumpOn);
 normal();step();step(3000);assert(O::extractionPumpOn && O::insertionPumpOn);
 readings(600,850);step();assert(O::extractionPumpOn && O::insertionPumpOn);
 readings(900,850);step();assert(!O::extractionPumpOn && !O::insertionPumpOn);
 readings(900,950);step();step(199);assert(!O::extractionPumpOn); // pH stabilization pause.
 step(1);step(100);assert(O::extractionPumpOn && O::insertionPumpOn && P::quality==2);
 readings(900,850);step();assert(!O::extractionPumpOn);
 // Both Low and Very Low refill, then continue through the Normal allowance.
 I::normalTriggered=false;I::lowTriggered=true;step();assert(O::insertionPumpOn);
 I::lowTriggered=false;step();assert(O::insertionPumpOn && !O::extractionPumpOn);
 normal();step();assert(O::insertionPumpOn);step(3000);assert(!O::insertionPumpOn);
 // Disabling the driving quality sensor cancels its local action.
 readings(100,850);step();step(100);assert(O::extractionPumpOn);
 cmd("MegaInput,SENSORS:59,REV:2");normal();step();assert(!O::extractionPumpOn && !O::insertionPumpOn);
 // A manual stop persists even when water quality is critical.
 cmd("MegaInput,SENSORS:63,REV:3");cmd("MegaOutput,PUMP:STOP_ALL");
 normal();readings(100,950);step(1000);assert(!O::extractionPumpOn && !P::autoMode);
 cmd("MegaOutput,WATERMODE:AUTO");step();step(100);assert(O::extractionPumpOn);
 // A staged config change stops automatic motion until a complete valid commit.
 cmd("MegaConfig,BEGIN:2,PL:6,PH:9,WL:6.5,WH:8.5,A6:900,A9:800,FAN:35,CONF:100,SETTLE:200,MAINT:1,ROWS:1");
 assert(!O::extractionPumpOn && !P::configured);
 cmd("MegaConfig,ROW:2,I:0,MIN:0,MAX:1023,CAT:0");cmd("MegaConfig,COMMIT:2");
 normal();readings(100,950);step();step(1000);assert(!O::extractionPumpOn);
 I::upperTriggered=true;step();assert(O::extractionPumpOn && !O::insertionPumpOn); // Level protection during calibration.
 cmd("MegaOutput,SYSTEM:OFF");assert(!O::extractionPumpOn && !I::systemEnabled && !P::autoMode);
 // Actual ADC service rejects three high and three low outliers.
 I::sensorMode=I::SensorMode::NORMAL;I::resetAcquisition();
 pinInputs[A1]=900;I::serviceAnalog(fakeMillis);
 int samples[15]={0,1023,900,900,1023,900,0,900,900,1023,900,0,900,900,900};
 for(int value:samples){fakeMillis+=20;pinInputs[A1]=value;I::serviceAnalog(fakeMillis);}
 assert(I::phRawADC==900 && I::phAverageADC==900);
 assert(I::calculatePH(I::phAverageADC)>6.85f && I::calculatePH(I::phAverageADC)<6.87f);
 std::cout<<"Mega local policy passed: atomic settings, Critical pH, Cloudy/Very Cloudy, confirmation, float priority, settling, disable, manual stop, calibration.\n";
}
