#pragma once
#include "Wire.h"
#include <iomanip>
#include <utility>
#define INPUT 0
#define INPUT_PULLUP 2
#define DEFAULT 1
inline int selectedAnalogReference = -1;
inline void analogReference(int mode) { selectedAnalogReference = mode; }
#define A0 14
#define A1 15
inline int pinInputs[20] = {};
inline int digitalReads[20] = {};
inline int analogReads[20] = {};
inline int analogRead(int pin) { ++analogReads[pin]; return pinInputs[pin]; }
inline int digitalRead(int pin) { ++digitalReads[pin]; return pinInputs[pin]; }
class String : public std::string {
public:
 using std::string::string;
 using std::string::operator=;
 String(const std::string& value) : std::string(value) {}
 String(float value, int digits) {
   std::ostringstream out; out << std::fixed << std::setprecision(digits) << value;
   assign(out.str());
 }
};

#define PSTR(s) s
#define strcmp_P strcmp
#define strncmp_P strncmp
