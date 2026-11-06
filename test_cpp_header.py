"""Compile and exercise the portable C++ codec in esp32/d100h_protocol.h."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HEADER = ROOT / 'esp32'

SOURCE = r'''
#include "d100h_protocol.h"
#include <cstdio>
#include <cstring>

static int failures = 0;
static void check(bool ok, const char* name) {
    if (!ok) {
        std::fprintf(stderr, "FAIL: %s\n", name);
        ++failures;
    }
}

static void zero_block(uint8_t* p) {
    const uint8_t block[8] = {0x38,0x90,0xc4,0x99,0xa3,0x60,0xaa,0xad};
    std::memcpy(p, block, 8);
}

int main() {
    const uint8_t down_wire[20] = {
        0x8e,0x8f,0x5e,0x16,0xec,0x67,0x28,0x1e,
        0x38,0x90,0xc4,0x99,0xa3,0x60,0xaa,0xad,0,0,0,0};
    const uint8_t up_wire[20] = {
        0x02,0x8d,0xb3,0x66,0x02,0x24,0xd0,0xc7,
        0x38,0x90,0xc4,0x99,0xa3,0x60,0xaa,0xad,0,0,0,0};
    d100h::Event event;

    check(d100h::decode(down_wire, sizeof down_wire, event) &&
          event.id == 1 && event.down, "BLE captured down");
    check(d100h::decode(up_wire, sizeof up_wire, event) &&
          event.id == 1 && !event.down, "BLE captured up");

    const uint8_t plain[5] = {0x06,0x01,0x23,0x00,0x01};
    const uint8_t heartbeat[20] = {
        0x24,0x56,0x9e,0xf2,0x28,0xe1,0x45,0xa1,
        0x38,0x90,0xc4,0x99,0xa3,0x60,0xaa,0xad,0,0,0,0};
    uint8_t encoded[20];
    d100h::encode(plain, sizeof plain, encoded);
    check(std::memcmp(encoded, heartbeat, sizeof encoded) == 0,
          "BLE heartbeat vector");
    check(std::memcmp(d100h::heartbeat, heartbeat, sizeof heartbeat) == 0,
          "BLE static heartbeat");

    for (uint8_t id = 1; id <= 12; ++id) {
        uint8_t input[20] = {0x8b,0x10,id,1};
        d100h::encode(input, sizeof input, encoded);
        check(d100h::decode(encoded, sizeof encoded, event) &&
              event.id == id && event.down, "BLE round-trip");
    }

    uint8_t usb[64];
    std::memcpy(usb, down_wire, 8);
    zero_block(usb + 8);
    for (unsigned i = 16; i < sizeof usb; i += 8) zero_block(usb + i);
    check(d100h::decode_usb(usb, sizeof usb, event) &&
          event.id == 1 && event.down, "USB captured frame");
    check(d100h::decode(usb, sizeof usb, event) == false,
          "BLE decoder rejects USB frame");

    uint8_t usb_tail[64];
    std::memcpy(usb_tail, usb, sizeof usb_tail);
    for (unsigned i = 16; i < sizeof usb_tail; ++i) usb_tail[i] = uint8_t(i);
    check(d100h::decode_usb(usb_tail, sizeof usb_tail, event) &&
          event.id == 1 && event.down, "USB non-zero tail");

    uint8_t usb_prefixed[65];
    usb_prefixed[0] = 0;
    std::memcpy(usb_prefixed + 1, usb, sizeof usb);
    check(d100h::decode_usb(usb_prefixed, sizeof usb_prefixed, event) &&
          event.id == 1 && event.down, "USB HIDAPI leading zero");
    check(d100h::decode_usb(down_wire, sizeof down_wire, event) == false,
          "USB decoder rejects BLE frame");

    uint8_t wired[64];
    const uint8_t wired_expected[64] = {
        0x24,0x56,0x9e,0xf2,0x28,0xe1,0x45,0xa1,
        0x38,0x90,0xc4,0x99,0xa3,0x60,0xaa,0xad};
    d100h::wired_heartbeat(wired);
    check(std::memcmp(wired, wired_expected, 16) == 0, "wired heartbeat prefix");
    for (unsigned i = 16; i < sizeof wired; i += 8) {
        uint8_t block[8];
        zero_block(block);
        check(std::memcmp(wired + i, block, 8) == 0, "wired heartbeat block");
    }

    return failures == 0 ? 0 : 1;
}
'''


class CppHeaderTests(unittest.TestCase):
    def test_header_vectors(self):
        compiler = shutil.which('c++') or shutil.which('g++')
        if compiler is None:
            self.skipTest('A C++ compiler is required to test the header')
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'test.cpp'
            binary = Path(temp) / 'test'
            source.write_text(SOURCE)
            subprocess.run([compiler, '-std=c++11', '-Wall', '-Wextra', '-Werror',
                            '-I' + str(HEADER), str(source), '-o', str(binary)],
                           check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
