// Host simulation only: this does not replace an AVR build or physical testing.
#include <cassert>
#include <iostream>
#include "../arduino/NANO_OUTPUT/NANO_OUTPUT.ino"
void command(const char* text) {
 char buf[180]; strncpy(buf,text,sizeof(buf)); buf[179]='\0'; handleFormattedMessage(buf);
}
int main() {
 setup();
 assert(!systemEnabled && !extractionPumpOn && !insertionPumpOn && !coolerIsOn);
 assert(pwm.channelPulse[FEEDER_CHANNEL]==4096);
 command("NANOOUTPUT,PUMP:BOTH_ON"); assert(!extractionPumpOn && !insertionPumpOn);
 command("NANOOUTPUT,SYSTEM:ON");
 assert(Serial.output.find("FEEDER:V3") != std::string::npos);
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:4,JOB:10");
 assert(queuedFeedCycles==4 && feederJob==10);
 assert(feederState==FeederState::HOLDING_OPEN);
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:2,JOB:11");
 assert(queuedFeedCycles==4 && feederJob==10);
 assert(Serial.output.find("FEED:REJECTED,JOB:11")!=std::string::npos);
 for(int i=0;i<8;i++){fakeMillis+=1000;updateFeeder(fakeMillis);}
 assert(queuedFeedCycles==0 && feederState==FeederState::IDLE);
 assert(Serial.output.find("FEED:COMPLETE,JOB:10")!=std::string::npos);
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:4294967296,JOB:12");
 assert(feederState==FeederState::IDLE);
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:1.5,JOB:12");
 assert(feederState==FeederState::IDLE);
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:-1,JOB:12");
 assert(feederState==FeederState::IDLE);
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:30,JOB:13");
 assert(queuedFeedCycles==30);
 for(int i=0;i<60;i++){fakeMillis+=1000;updateFeeder(fakeMillis);}
 assert(feederState==FeederState::IDLE);
 assert(Serial.output.find("FEED:COMPLETE,JOB:13")!=std::string::npos);
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:65536,JOB:13");
 assert(queuedFeedCycles==65536);
 command("NANOOUTPUT,FEED:STOP,JOB:0");
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:4294967295,JOB:13");
 assert(queuedFeedCycles==UINT32_MAX);
 command("NANOOUTPUT,FEED:STOP,JOB:0");
 assert(queuedFeedCycles==0 && feederState==FeederState::IDLE);
 assert(Serial.output.find("FEED:STOPPED,JOB:0")!=std::string::npos);
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:2,JOB:14");
 command("NANOOUTPUT,PUMP:EXTRACT_ON");
 assert(feederState==FeederState::IDLE && extractionPumpOn);
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:2,JOB:15");
 assert(feederState==FeederState::IDLE);
 assert(Serial.output.find("FEED:REJECTED,JOB:15")!=std::string::npos);
 command("NANOOUTPUT,PUMP:STOP_ALL");
 // Full pair command and immediate single-channel overrides.
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:3,JOB:16");
 command("NANOOUTPUT,PUMP:BOTH_ON");
 assert(extractionPumpOn && insertionPumpOn && feederState==FeederState::IDLE);
 command("NANOOUTPUT,PUMP:EXTRACT_ON");
 assert(extractionPumpOn && !insertionPumpOn);
 command("NANOOUTPUT,PUMP:BOTH_ON");
 assert(extractionPumpOn && insertionPumpOn);
 command("NANOOUTPUT,PUMP:INSERT_ON");
 assert(!extractionPumpOn && insertionPumpOn);
 command("NANOOUTPUT,PUMP:BOTH_ON");
 command("NANOOUTPUT,FEED:CUSTOM,CYCLES:2,JOB:17");
 assert(feederState==FeederState::IDLE);
 command("NANOOUTPUT,PUMP:STOP_ALL");
 assert(!extractionPumpOn && !insertionPumpOn);
 // Sustained warning temperatures must not activate the automatic fan.
 for (int i=0;i<8;++i) {fakeMillis+=1000;command("NANOOUTPUT,TEMP:34.9");}
 assert(!coolerIsOn && !coolerChangePending);
 // Critical boundary starts confirmation; warning cancels it.
 command("NANOOUTPUT,TEMP:35");assert(coolerChangePending);
 command("NANOOUTPUT,TEMP:34.9");assert(!coolerChangePending);
 command("NANOOUTPUT,TEMP:40");
 assert(!coolerIsOn);
 for (int i=0; i<5; ++i) {fakeMillis+=1000; command("NANOOUTPUT,TEMP:40");}
 assert(coolerIsOn);
 command("NANOOUTPUT,TEMPSENSOR:OFF");
 assert(!coolerIsOn && !temperatureSensorEnabled && !temperatureIsValid);
 Serial.output.clear();
 fakeMillis += TEMPERATURE_DATA_TIMEOUT_MS + 1;
 updateCoolerControl(fakeMillis);
 assert(Serial.output.find("STALE_TEMPERATURE") == std::string::npos);
 command("NANOOUTPUT,TEMP:40");
 assert(!coolerIsOn && !temperatureIsValid);
 command("NANOOUTPUT,TEMPSENSOR:ON");
 assert(!coolerIsOn && !temperatureIsValid);
 command("NANOOUTPUT,TEMP:40");
 assert(!coolerIsOn);
 for (int i=0; i<5; ++i) {fakeMillis+=1000; command("NANOOUTPUT,TEMP:40");}
 assert(coolerIsOn);
 fakeMillis += TEMPERATURE_DATA_TIMEOUT_MS + 1;
 updateCoolerControl(fakeMillis);
 assert(!coolerIsOn && Serial.output.find("STALE_TEMPERATURE") != std::string::npos);
 // A lone spike cannot switch the fan on merely because five seconds pass.
 command("NANOOUTPUT,TEMP:40");
 fakeMillis += 5000; updateCoolerControl(fakeMillis);
 assert(!coolerIsOn);
 // A new reading after the gap starts a new confirmation window.
 command("NANOOUTPUT,TEMP:40");
 assert(!coolerIsOn);
 fakeMillis += 1000; command("NANOOUTPUT,TEMP:27");
 assert(!coolerChangePending && !coolerIsOn);
 for(int i=0;i<6;++i){fakeMillis+=1000; command("NANOOUTPUT,TEMP:36");}
 assert(coolerIsOn);
 // Hysteresis plus five-second normal-temperature confirmation.
 command("NANOOUTPUT,TEMP:34.4");
 fakeMillis += 1000; command("NANOOUTPUT,TEMP:34.8");
 assert(coolerIsOn && !coolerChangePending);
 for(int i=0;i<6;++i){fakeMillis+=1000; command("NANOOUTPUT,TEMP:34.4");}
 assert(!coolerIsOn);
 command("NANOOUTPUT,TEMP:40");
 fakeMillis += 2000; command("NANOOUTPUT,TEMP:40");
 command("NANOOUTPUT,TEMP:INVALID");
 assert(!coolerChangePending);
 fakeMillis += 1000; command("NANOOUTPUT,TEMP:40");
 assert(!coolerIsOn);
 command("NANOOUTPUT,FANMODE:ON");
 assert(coolerIsOn && fanMode == FanMode::ON);
 command("NANOOUTPUT,TEMPSENSOR:OFF");
 fakeMillis += 20000; updateCoolerControl(fakeMillis);
 assert(coolerIsOn); // Explicit manual ON is independent of disabled sensor.
 command("NANOOUTPUT,FANMODE:OFF");
 assert(!coolerIsOn);
 command("NANOOUTPUT,TEMPSENSOR:ON");
 for(int i=0;i<6;++i){fakeMillis+=1000; command("NANOOUTPUT,TEMP:40");}
 assert(!coolerIsOn); // OFF overrides hot readings.
 command("NANOOUTPUT,FANMODE:AUTO");
 assert(!temperatureIsValid && !coolerIsOn);
 for(int i=0;i<6;++i){fakeMillis+=1000; command("NANOOUTPUT,TEMP:40");}
 assert(coolerIsOn);
 command("NANOOUTPUT,HEARTBEAT:1");
 Serial.output.clear(); fakeMillis += 2000; loop();
 assert(Serial.output.find("FANCTRL:V1,FANMODE:AUTO") != std::string::npos);
 // All-off overrides manual fan, both pumps, camera and feeder commands.
 command("NANOOUTPUT,PUMP:BOTH_ON");command("NANOOUTPUT,FANMODE:ON");
 command("NANOOUTPUT,SYSTEM:OFF,PUMP:BOTH_ON");
 assert(!systemEnabled && !coolerIsOn && !extractionPumpOn && !insertionPumpOn);
 assert(pinOutputs[8]==LOW && pinOutputs[9]==LOW && pinOutputs[5]==LOW);
 assert(pwm.channelPulse[PAN_CHANNEL]==4096 && pwm.channelPulse[TILT_CHANNEL]==4096);
 command("NANOOUTPUT,PUMP:BOTH_ON,FANMODE:ON,FEED:YES,CAMERA:CENTER");
 assert(!extractionPumpOn && !coolerIsOn && feederState==FeederState::IDLE);
 fakeMillis+=1000;loop();assert(pwm.channelPulse[FEEDER_CHANNEL]==4096);
 command("NANOOUTPUT,HEARTBEAT:1");assert(!systemEnabled);
 command("NANOOUTPUT,SYSTEM:ON");command("NANOOUTPUT,PUMP:BOTH_ON");
 fakeMillis+=3999;loop();assert(extractionPumpOn && insertionPumpOn);
 fakeMillis+=1;loop();assert(!systemEnabled && !extractionPumpOn && !insertionPumpOn);
 assert(Serial.output.find("REASON:HOST_TIMEOUT")!=std::string::npos);
 command("NANOOUTPUT,SYSTEM:ON");command("NANOOUTPUT,FEED:CUSTOM,CYCLES:30,JOB:90");
 fakeMillis+=4000;loop();assert(feederState==FeederState::IDLE && queuedFeedCycles==0);
 fakeMillis+=1000;loop();assert(pwm.channelPulse[FEEDER_CHANNEL]==4096);
 fakeMillis=UINT32_MAX-100;command("NANOOUTPUT,SYSTEM:ON");command("NANOOUTPUT,PUMP:BOTH_ON");
 fakeMillis=uint32_t(fakeMillis+4000);loop();assert(!systemEnabled && !extractionPumpOn);
 std::cout << "Firmware simulation passed: cycles, completion, bounds, overlap, stop, both-pump transitions and feeder/pump interlock.\n";
}
