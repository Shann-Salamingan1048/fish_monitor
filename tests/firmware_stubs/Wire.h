#pragma once
#include <cstdint>
#include <string>
#include <sstream>
#include <iomanip>
#define HIGH 1
#define LOW 0
#define OUTPUT 1
class __FlashStringHelper {};
#define F(s) reinterpret_cast<const __FlashStringHelper *>(s)
inline unsigned long fakeMillis = 0;
inline unsigned long millis() {return fakeMillis;}
inline void delay(unsigned long d) {fakeMillis += d;}
inline int pinOutputs[20] = {};
inline void digitalWrite(int pin, int value) {pinOutputs[pin]=value;}
inline void pinMode(int, int) {}
inline int constrain(int v, int a, int b) {return v<a?a:v>b?b:v;}
inline long map(long v, long a, long b, long c, long d) {return (v-a)*(d-c)/(b-a)+c;}
struct FakeWire {void begin(){}};
inline FakeWire Wire;
class Print {
public:
 virtual ~Print() = default;
 virtual size_t write(uint8_t byte) = 0;
 size_t write(const uint8_t* data, size_t count) {for(size_t i=0;i<count;++i) write(data[i]);return count;}
 void print(const __FlashStringHelper* s) {print(reinterpret_cast<const char*>(s));}
 void print(const char* s) {while(*s)write(*s++);}
 template<class T> void print(T v){std::ostringstream stream;stream<<v;print(stream.str().c_str());}
 void print(float v, int digits){std::ostringstream stream;stream<<std::fixed<<std::setprecision(digits)<<v;print(stream.str().c_str());}
 void print(uint8_t v){print(static_cast<unsigned>(v));}
 void println(){write('\n');}
 template<class T> void println(T v){print(v);write('\n');}
 void println(float v,int digits){print(v,digits);write('\n');}
};
struct FakeSerial : Print {
 std::string output,input;
 int txSpace = 64;
 void begin(int) {}
 int available(){return input.size();}
 int read(){if(input.empty())return -1;char c=input[0];input.erase(0,1);return c;}
 int availableForWrite(){return txSpace;}
 using Print::write;
 size_t write(uint8_t byte) override {output+=char(byte);return 1;}
};
inline FakeSerial Serial;
