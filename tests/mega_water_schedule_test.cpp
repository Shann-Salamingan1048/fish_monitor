#include <cassert>
#include <iostream>
#include "../arduino/AquaPi_Mega/AquaPi_Mega.ino"
using namespace AquaPi;
namespace P=AquaPi::Policy;namespace I=AquaPi::MegaInput;namespace O=AquaPi::MegaOutput;
void cmd(const char *text){char b[256];strncpy(b,text,255);b[255]=0;dispatch(b);}
void level(bool low,bool normal,bool upper){
 pinInputs[9]=low?LOW:HIGH;pinInputs[8]=normal?LOW:HIGH;pinInputs[7]=upper?LOW:HIGH;
 I::readWaterFloat();
}
void step(uint32_t ms=1){fakeMillis+=ms;P::service(fakeMillis);}
void ready(){
 cmd("MegaOutput,PUMP:STOP_ALL");cmd("MegaOutput,SYSTEM:ON");cmd("MegaInput,SENSORS:63,REV:1");
 P::configured=true;P::active.maintenance=false;P::active.settleMs=120000;
 cmd("MegaOutput,WATERMODE:AUTO");level(true,true,false);
}
int main(){
 begin();ready();
 cmd("MegaOutput,WATERCHANGE:START,JOB:1,DRAIN:5,REFILL:10");
 assert(P::wcPhase==1 && O::extractionPumpOn && !O::insertionPumpOn);
 uint32_t began=P::wcSince;
 level(true,false,false);step(2000);
 cmd("MegaOutput,WATERCHANGE:START,JOB:1,DRAIN:5,REFILL:10");assert(P::wcSince==began);
 cmd("MegaOutput,WATERCHANGE:START,JOB:2,DRAIN:5,REFILL:10");assert(P::wcJob==1 && P::wcPhase==1);
 level(false,false,false);step(); // Early lower float cut-off, before 5 seconds.
 assert(P::wcPhase==2 && O::insertionPumpOn && !O::extractionPumpOn);
 level(true,true,false);step();assert(!P::wcPhase && !O::insertionPumpOn && P::autoMode);
 assert(!strcmp(P::wcStatus,"COMPLETE") && P::settling);
 cmd("MegaOutput,WATERCHANGE:START,JOB:1,DRAIN:5,REFILL:10");assert(!P::wcPhase); // No completed-job replay.
 ready();cmd("MegaOutput,WATERCHANGE:START,JOB:3,DRAIN:2,REFILL:3");
 level(true,false,false);step(2000);assert(P::wcPhase==2 && O::insertionPumpOn);
 step(3000);assert(!P::wcPhase && !P::autoMode && !O::insertionPumpOn);
 assert(!strcmp(P::wcReason,"REFILL_TIMEOUT"));
 ready();cmd("MegaOutput,WATERCHANGE:START,JOB:4,DRAIN:2,REFILL:3");
 level(true,true,true);step();assert(!P::wcPhase && !O::extractionPumpOn && !O::insertionPumpOn && !P::autoMode);
 ready();cmd("MegaOutput,WATERCHANGE:START,JOB:5,DRAIN:2,REFILL:3");
 level(false,true,false);step();assert(!P::wcPhase && !strcmp(P::wcReason,"FLOATS_OR_CONFIG"));
 ready();cmd("MegaOutput,WATERCHANGE:START,JOB:6,DRAIN:2,REFILL:3");
 cmd("MegaInput,SENSORS:31,REV:2");assert(!P::wcPhase && !O::extractionPumpOn);
 ready();cmd("MegaOutput,WATERCHANGE:START,JOB:7,DRAIN:2,REFILL:3");
 cmd("MegaOutput,WATERCHANGE:STOP");assert(!P::wcPhase && !P::autoMode && !O::extractionPumpOn);
 ready();cmd("MegaOutput,WATERCHANGE:START,JOB:8,DRAIN:2,REFILL:3");
 cmd("MegaOutput,SYSTEM:OFF");assert(!P::wcPhase && !O::systemEnabled && !O::extractionPumpOn);
 ready();cmd("MegaOutput,WATERCHANGE:START,JOB:9,DRAIN:10,REFILL:10");
 fakeMillis+=4000;tick();assert(!P::wcPhase && !O::systemEnabled && !O::extractionPumpOn);
 ready();cmd("MegaOutput,FEED:CUSTOM,CYCLES:3,JOB:50");
 cmd("MegaOutput,WATERCHANGE:START,JOB:10,DRAIN:2,REFILL:3");assert(!P::wcPhase && O::feederState!=O::FeederState::IDLE);
 cmd("MegaOutput,FEED:STOP,JOB:0");
 ready();P::active.maintenance=true;cmd("MegaOutput,WATERCHANGE:START,JOB:11,DRAIN:2,REFILL:3");assert(!P::wcPhase);
 ready();cmd("MegaOutput,WATERCHANGE:START,JOB:12,DRAIN:601,REFILL:3");assert(!P::wcPhase);
 ready();fakeMillis=UINT32_MAX-1000;cmd("MegaOutput,WATERCHANGE:START,JOB:13,DRAIN:2,REFILL:3");
 level(true,false,false);step(2000);assert(P::wcPhase==2);
 level(true,true,false);step();assert(!P::wcPhase && !O::insertionPumpOn);
 // UART congestion cannot block a local refill timeout.
 ready();cmd("MegaOutput,WATERCHANGE:START,JOB:14,DRAIN:1,REFILL:1");level(true,false,false);Serial.txSpace=0;
 step(1000);assert(P::wcPhase==2);step(1000);assert(!P::wcPhase && !O::insertionPumpOn);
 std::cout<<"Scheduled water change passed: drain cutoff, refill target, timeout, overflow, invalid floats, cancel, off, watchdog, replay, conflicts, rollover and UART backpressure.\n";
}
