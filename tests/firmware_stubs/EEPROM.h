#pragma once
#include <cstdint>
#include <cstring>
#include <cassert>
struct FakeEEPROM {
 uint8_t bytes[1024];
 unsigned puts = 0;
 FakeEEPROM() { memset(bytes, 0xff, sizeof(bytes)); }
 template<class T> T& get(int address, T& value) {
  assert(address >= 0 && address + sizeof(T) <= sizeof(bytes));
  memcpy(&value, bytes + address, sizeof(T)); return value;
 }
 template<class T> const T& put(int address, const T& value) {
  assert(address >= 0 && address + sizeof(T) <= sizeof(bytes));
  memcpy(bytes + address, &value, sizeof(T)); ++puts; return value;
 }
};
inline FakeEEPROM EEPROM;
