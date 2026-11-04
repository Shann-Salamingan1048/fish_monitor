#include <cassert>
#include <cmath>
#include <iostream>
#include "../arduino/AquaPi_Mega/AquaPi_Mega.ino"
using namespace AquaPi;
namespace P=AquaPi::Policy;namespace I=AquaPi::MegaInput;
void cmd(const std::string &s){char b[256];strncpy(b,s.c_str(),255);b[255]=0;dispatch(b);}
void cfg(int rev,int points,int pair,float a4,float a6,float a9){
 std::string r=std::to_string(rev);
 cmd("MegaConfig,BEGIN:"+r+",PL:6,PH:9,WL:6.5,WH:8.5,A6:"+std::to_string(a6)+",A9:"+std::to_string(a9)+",PC:"+std::to_string(points)+",PP:"+std::to_string(pair)+",A4:"+std::to_string(a4)+",FAN:35,CONF:100,SETTLE:200,MAINT:0,ROWS:1");
 cmd("MegaConfig,ROW:"+r+",I:0,MIN:0,MAX:1023,CAT:3");cmd("MegaConfig,COMMIT:"+r);
 for(int i=0;i<100;++i)link.drain();
}
bool near(float a,float b){return std::fabs(a-b)<0.002f;}
void batch(int center,bool outliers=false){
 fakeMillis+=2000;I::serviceAnalog(fakeMillis);
 for(int i=0;i<15;++i){fakeMillis+=20;pinInputs[A1]=outliers && i<3 ? 0 : outliers && i<6 ? 1023:center;I::serviceAnalog(fakeMillis);}
 assert(I::adcTask==I::AdcTask::IDLE && I::phReadingReady);
}
int main(){
 begin();cmd("MegaOutput,SYSTEM:ON");
 cfg(1,2,1,1000.25f,900.125f,0);assert(P::configured && I::runtimePhValid);
 assert(near(I::calculatePH(1000.25f),4.01f));assert(near(I::calculatePH(900.125f),6.86f));
 assert(near(I::calculatePH((1000.25f+900.125f)/2),5.435f));
 cfg(2,2,2,1000.25f,0,800.5f);assert(P::configured && I::runtimePhValid);
 assert(near(I::calculatePH(1000.25f),4.01f));assert(near(I::calculatePH(800.5f),9.18f));
 assert(near(I::calculatePH((1000.25f+800.5f)/2),6.595f));
 cfg(3,2,0,0,900.125f,800.5f);assert(P::configured && I::runtimePhValid);
 assert(near(I::calculatePH(900.125f),6.86f));assert(near(I::calculatePH(800.5f),9.18f));
 assert(near(I::calculatePH((900.125f+800.5f)/2),8.02f));
 cfg(4,3,0,1000.25f,900.125f,800.5f);assert(P::configured && I::runtimePhValid);
 assert(near(I::calculatePH(1000.25f),4.01f));assert(near(I::calculatePH(900.125f),6.86f));assert(near(I::calculatePH(800.5f),9.18f));
 // The join is continuous and the whole curve is monotone, with no polynomial overshoot.
 float prev=I::calculatePH(800.5f);
 for(float adc=800.5f;adc<=1000.25f;adc+=0.25f){float ph=I::calculatePH(adc);assert(ph<=prev+0.0001f && ph>=4.0f && ph<=9.19f);prev=ph;}
 assert(std::fabs(I::calculatePH(900.124f)-I::calculatePH(900.126f))<0.001f);
 // Switching back replaces every slope/origin: no stale third-point segment.
 cfg(5,2,2,1000,0,800);assert(near(I::calculatePH(900),6.595f));
 cfg(6,2,1,910,900,0);assert(!P::configured);
 cfg(7,2,2,800,0,1000);assert(!P::configured);
 cfg(8,2,7,1000,900,800);assert(!P::configured);
 cfg(9,3,1,1000,900,800);assert(!P::configured);
 cfg(10,3,0,1000,0,800);assert(!P::configured);
 cfg(11,2,0,0,0,0);assert(P::configured && !I::runtimePhValid && std::isnan(I::calculatePH(900)));
 cfg(12,2,0,0,900,800);I::sensorMode=I::SensorMode::NORMAL;I::resetAcquisition();
 batch(900,true);assert(I::phRawADC==900 && I::phAverageADC==900); // Six extreme spikes rejected.
 batch(800,true);assert(I::phRawADC==800 && near(I::phAverageADC,880));
 assert(near(I::calculatePH(I::phAverageADC),7.324f));
 float previous=I::phAverageADC;
 for(int i=0;i<24;++i){batch(800);assert(I::phAverageADC<=previous && I::phAverageADC>=800);previous=I::phAverageADC;}
 assert(near(I::calculatePH(I::phAverageADC),9.18f) || std::fabs(I::calculatePH(I::phAverageADC)-9.18f)<0.01f);
 std::cout<<"pH pairs passed: all pair endpoints/midpoints, 3-point continuity, invalid settings, mode switching, outlier rejection and smooth convergence.\n";
}
