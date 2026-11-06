#pragma once
// Portable D100H codec; usable from Arduino or ESP-IDF C++.
// BLE vendor channel (20-byte values) and wired USB HID (64-byte reports).
// Protocol: https://github.com/kriomant/ulanzi-d100h/blob/main/PROTOCOL.md
// The BLE and wired USB protocol paths were live-validated on an ESP32-P4
// host (ESP-IDF 5.4.2, NimBLE over ESP-Hosted); this header is checked
// against captured vectors. See esp32/INTEGRATION.txt for integration notes.
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
inline void encrypt(uint8_t* data, size_t size) {
    const uint32_t key[4] = {0xcaa5baca,0xbc2a8a6d,0xca5a9eba,0x9bb88bca};
    for (size_t offset=0; offset+8<=size; offset+=8) {
        uint32_t a=load32(data+offset), b=load32(data+offset+4);
        uint32_t sum=0;
        for (unsigned round=0; round<32; ++round) {
            sum += 0x9e3779b9;
            a += ((b<<4)+key[0]) ^ (b+sum) ^ ((b>>5)+key[1]);
            b += ((a<<4)+key[2]) ^ (a+sum) ^ ((a>>5)+key[3]);
        }
        store32(data+offset,a); store32(data+offset+4,b);
    }
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

// Event occupies the first eight-byte block. BLE values have 20 bytes and
// require all padding bytes to be zero; wired USB reports only guarantee the
// first block because later blocks can contain unrelated non-zero data.
inline bool parse_event(const uint8_t* p, size_t padding_end, Event& event) {
    if (padding_end < 8) return false;
    if (p[0]!=0x8b || p[1]!=0x10 || p[2]<1 || p[2]>12 || p[3]>1)
        return false;
    for (unsigned i=4;i<padding_end;++i) if (p[i]) return false;
    if (p[2]>=9 && p[3]!=1) return false;
    event.id=p[2]; event.down=p[3]!=0;
    return true;
}

// BLE vendor notification (20 bytes).
inline bool decode(const uint8_t* wire, size_t size, Event& event) {
    if (size != 20) return false;
    uint8_t p[20]; memcpy(p,wire,20); decrypt(p,20);
    return parse_event(p,20,event);
}

// Wired USB vendor report (64 bytes, or 65 with a HIDAPI-style leading zero).
inline bool decode_usb(const uint8_t* wire, size_t size, Event& event) {
    if (size == 65 && wire[0] == 0) { ++wire; --size; }
    if (size != 64) return false;
    uint8_t p[64]; memcpy(p,wire,64); decrypt(p,64);
    return parse_event(p,8,event);
}

// Encrypt a plaintext vendor command into a 20-byte BLE value. Zero-pads to
// 20 bytes; TEA encrypts the first 16 bytes, and bytes 16..19 are passed
// through unchanged (zero for the heartbeat and sleep commands).
inline void encode(const uint8_t* plain, size_t size, uint8_t out[20]) {
    memset(out,0,20);
    if (plain != NULL && size > 0) {
        if (size > 20) size = 20;
        memcpy(out,plain,size);
    }
    encrypt(out,20);
}

// Wired USB heartbeat: plaintext 06 01 23 00 01 plus zeros, then all eight
// blocks encrypted. Do not zero-pad the 20-byte BLE ciphertext instead.
inline void wired_heartbeat(uint8_t out[64]) {
    memset(out,0,64);
    out[0]=0x06; out[1]=0x01; out[2]=0x23; out[3]=0x00; out[4]=0x01;
    encrypt(out,64);
}
} // namespace d100h
