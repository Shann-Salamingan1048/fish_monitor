#pragma once
struct Adafruit_PWMServoDriver {
 int lastChannel=-1, lastPulse=-1, channelPulse[16]={};
 void begin(){}
 void setPWMFreq(int){}
 void setPWM(int channel, int, int pulse){lastChannel=channel;lastPulse=pulse;channelPulse[channel]=pulse;}
};
