#!/usr/bin/env python3
"""D100H event listener: BLE vendor channel or experimental Linux hidraw.

BLE: python d100h.py ble AA:BB:CC:DD:EE:FF [--map mappings.json]
HID: python d100h.py list-hid
     python d100h.py hid /dev/hidrawN --report-id 0xfb --size 64

Close Ulanzi Studio first. BLE requires `pip install bleak`; hidraw and codec
use only Python's standard library. USB report ID/size must match the descriptor.
Mappings are JSON objects from event names to command argument arrays, e.g.
{"top_left.down": ["playerctl", "play-pause"]}. No shell is used.
Protocol sources: kriomant/ulanzi-d100h (MIT), ftl/hamdial (MIT), and static
analysis of Ulanzi Studio 3.4.8. No hardware validation of this client performed.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import time

KEY = (0xCAA5BACA, 0xBC2A8A6D, 0xCA5A9EBA, 0x9BB88BCA)
MASK = 0xFFFFFFFF
BUTTONS = {1: 'top_left', 2: 'top_middle', 3: 'top_right', 4: 'upper_left',
           5: 'upper_right', 6: 'lower_left', 8: 'lower_right'}
SERVICE = '00001971-0000-1000-8000-00805f9b34fb'
WRITE = '0000fd01-0000-1000-8000-00805f9b34fb'
NOTIFY = '0000fd00-0000-1000-8000-00805f9b34fb'


def crypt(data, encrypt=False):
    """TEA each complete 8-byte block; leave trailing bytes unchanged."""
    out = bytearray(data)
    k0, k1, k2, k3 = KEY
    for offset in range(0, len(out) - 7, 8):
        a, b = struct.unpack_from('<II', out, offset)
        total = 0 if encrypt else 0xC6EF3720
        for _ in range(32):
            if encrypt:
                total = (total + 0x9E3779B9) & MASK
                a = (a + (((b << 4) + k0) ^ (b + total) ^ ((b >> 5) + k1))) & MASK
                b = (b + (((a << 4) + k2) ^ (a + total) ^ ((a >> 5) + k3))) & MASK
            else:
                b = (b - (((a << 4) + k2) ^ (a + total) ^ ((a >> 5) + k3))) & MASK
                a = (a - (((b << 4) + k0) ^ (b + total) ^ ((b >> 5) + k1))) & MASK
                total = (total - 0x9E3779B9) & MASK
        struct.pack_into('<II', out, offset, a, b)
    return bytes(out)


def heartbeat(size=20):
    if size < 20:
        raise ValueError('Vendor payload must be at least 20 bytes')
    return crypt(bytes.fromhex('0601230001').ljust(size, b'\0'), True)


class Dispatcher:
    def __init__(self, mapping=None):
        self.held = set()
        self.mapping = mapping or {}
        self.children = []
        for name, argv in self.mapping.items():
            if not isinstance(argv, list) or not argv or not all(isinstance(s, str) for s in argv):
                raise ValueError(f'{name}: expected a nonempty command argument array')

    def receive(self, wire):
        if len(wire) < 20:
            print(json.dumps({'short_packet': wire.hex()}), flush=True)
            return
        p = crypt(wire)
        if p[:2] != b'\x8b\x10':
            if p[:4] != bytes.fromhex('06012310'):
                print(json.dumps({'other_plaintext': p.hex()}), flush=True)
            return
        control, state = p[2:4]
        if control in BUTTONS and state in (0, 1):
            button = BUTTONS[control]
            if state:
                self.held.add(button)
            name = button + ('.down' if state else '.up')
        elif control == 7 and state in (0, 1):
            if not state:  # Synthetic release pair: execute one click only.
                return
            name = 'knob.click'
        elif control in (9, 10, 11, 12) and state == 1:
            name = ('held_rotate.' if control >= 11 else 'rotate.') + ('cw' if control % 2 == 0 else 'ccw')
        else:
            print(json.dumps({'unknown_event': p.hex()}), flush=True)
            return
        print(json.dumps({'event': name, 'id': control, 'held_buttons': sorted(self.held)}), flush=True)
        if control in BUTTONS and not state:
            self.held.discard(BUTTONS[control])
        # Reap completed commands, and launch configured actions without a shell.
        self.children = [child for child in self.children if child.poll() is None]
        if name in self.mapping:
            try:
                self.children.append(subprocess.Popen(self.mapping[name]))
            except OSError as exc:
                print(json.dumps({'action_error': str(exc), 'event': name}), flush=True)


async def run_ble(args, dispatch):
    from bleak import BleakClient
    async with BleakClient(args.address) as client:
        service = client.services.get_service(SERVICE)
        if service is None or service.get_characteristic(WRITE) is None or service.get_characteristic(NOTIFY) is None:
            raise RuntimeError('Expected D100H vendor channel 1971/FD01/FD00 not found')
        await client.start_notify(NOTIFY, lambda _sender, value: dispatch.receive(bytes(value)))
        try:
            while client.is_connected:
                await client.write_gatt_char(WRITE, heartbeat(), response=False)
                await asyncio.sleep(1)
        finally:
            dispatch.held.clear()
            if client.is_connected:
                await client.stop_notify(NOTIFY)


def list_hid():
    # No identity assumptions: show all nodes and their report descriptors.
    for entry in sorted(Path('/sys/class/hidraw').glob('hidraw*')):
        try:
            print('/dev/' + entry.name)
            print((entry / 'device/uevent').read_text().strip())
            print('REPORT_DESCRIPTOR=' + (entry / 'device/report_descriptor').read_bytes().hex())
        except OSError as exc:
            print(exc)


def run_hid(args, dispatch):
    """Experimental: caller must select vendor interface and descriptor sizes."""
    if not 0 <= args.report_id <= 255 or not 20 <= args.size <= 64:
        raise ValueError('Report ID must be 0..255; payload size must be 20..64')
    fd = os.open(args.path, os.O_RDWR | os.O_NONBLOCK)
    try:
        next_beat = 0
        while True:
            now = time.monotonic()
            if now >= next_beat:
                # hidraw writes always start with report ID, including zero.
                packet = bytes([args.report_id]) + heartbeat(args.size)
                if os.write(fd, packet) != len(packet):
                    raise OSError('Short hidraw write')
                next_beat = now + 1
            ready, _, _ = select.select([fd], [], [], max(0, next_beat - time.monotonic()))
            if ready:
                raw = os.read(fd, 4096)
                if not raw:
                    raise OSError('Device disconnected')
                # Linux reads include an ID only for numbered reports.
                if args.report_id:
                    if raw[0] != args.report_id:
                        continue
                    raw = raw[1:]
                dispatch.receive(raw)
    finally:
        dispatch.held.clear()
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='mode', required=True)
    sub.add_parser('list-hid', help='Read Linux HID identities and report descriptors')
    ble = sub.add_parser('ble', help='BLE vendor channel; requires bleak')
    ble.add_argument('address', help='Bluetooth address, already paired through the OS')
    hid = sub.add_parser('hid', help='Experimental Linux vendor HID transport')
    hid.add_argument('path')
    hid.add_argument('--report-id', type=lambda s: int(s, 0), required=True)
    hid.add_argument('--size', type=int, required=True, help='Payload bytes, excluding report ID')
    for item in (ble, hid):
        item.add_argument('--map', type=Path, help='Optional JSON command mapping')
    args = parser.parse_args()
    if args.mode == 'list-hid':
        list_hid()
        return
    mapping = json.loads(args.map.read_text()) if args.map else {}
    if not isinstance(mapping, dict):
        parser.error('Mapping must be a JSON object')
    dispatch = Dispatcher(mapping)
    try:
        if args.mode == 'ble':
            asyncio.run(run_ble(args, dispatch))
        else:
            run_hid(args, dispatch)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
