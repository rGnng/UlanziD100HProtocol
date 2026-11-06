#pragma once
// Portable D100H BLE codec; usable from Arduino or ESP-IDF C++.
// Protocol: https://github.com/kriomant/ulanzi-d100h/blob/main/PROTOCOL.md
// Verified offline with captured vectors; not tested on an ESP32/device.
#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace d100h {
static const char* const service_uuid = "00001971-0000-1000-8000-00805f9b34fb";
static const char* const write_uuid = "0000fd01-0000-1000-8000-00805f9b34fb";
static const char* const notify_uuid = "0000fd00-0000-1000-8000-00805f9b34fb";
// Already encrypted 20-byte command. Write WITHOUT response after subscribing,
// immediately and every 1000 ms. BLE value contains no HID report ID.
static const uint8_t heartbeat[20] = {
    0x24,0x56,0x9e,0xf2,0x28,0xe1,0x45,0xa1,
    0x38,0x90,0xc4,0x99,0xa3,0x60,0xaa,0xad,0,0,0,0
};

inline uint32_t load32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 |
           uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
inline void store32(uint8_t* p, uint32_t v) {
    for (unsigned i=0; i<4; ++i) p[i] = uint8_t(v >> (8*i));
}
inline void decrypt(uint8_t* data, size_t size) {
    const uint32_t key[4] = {0xcaa5baca,0xbc2a8a6d,0xca5a9eba,0x9bb88bca};
    for (size_t offset=0; offset+8<=size; offset+=8) {
        uint32_t a=load32(data+offset), b=load32(data+offset+4);
        uint32_t sum=0xc6ef3720;
        for (unsigned round=0; round<32; ++round) {
            b -= ((a<<4)+key[2]) ^ (a+sum) ^ ((a>>5)+key[3]);
            a -= ((b<<4)+key[0]) ^ (b+sum) ^ ((b>>5)+key[1]);
            sum -= 0x9e3779b9;
        }
        store32(data+offset,a); store32(data+offset+4,b);
    }
}

// 01 top-left, 02 top-middle, 03 top-right, 04 upper-left,
// 05 upper-right, 06 lower-left, 07 knob click, 08 lower-right,
// 09 CCW, 0a CW, 0b held-knob CCW, 0c held-knob CW.
struct Event { uint8_t id; bool down; };
inline bool decode(const uint8_t* wire, size_t size, Event& event) {
    if (size != 20) return false;
    uint8_t p[20]; memcpy(p,wire,20); decrypt(p,20);
    if (p[0]!=0x8b || p[1]!=0x10 || p[2]<1 || p[2]>12 || p[3]>1)
        return false;
    for (unsigned i=4;i<20;++i) if (p[i]) return false;
    if (p[2]>=9 && p[3]!=1) return false;
    event.id=p[2]; event.down=p[3]!=0;
    return true;
}
} // namespace d100h
