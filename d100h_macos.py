#!/usr/bin/env python3
"""macOS D100H discovery, BLE listener, and experimental vendor HID access.

Install: python3 -m pip install -r requirements-macos.txt
Inspect: python3 d100h_macos.py usb
         python3 d100h_macos.py list-hid
         python3 d100h_macos.py probe-hid --path PATH_HEX
Listen:  python3 d100h_macos.py scan
         python3 d100h_macos.py ble [DEVICE-UUID] [--map mappings.macos.example.json]

Quit Ulanzi Studio before listening. For BLE, disconnect USB and allow Bluetooth
access for your terminal in System Settings > Privacy & Security > Bluetooth.
The experimental hid command requires report ID and size from your descriptor.
"""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time

from d100h import Dispatcher, SERVICE, add_sleep_options, heartbeat, run_ble


def load_mapping(path):
    mapping = json.loads(path.read_text()) if path else {}
    if not isinstance(mapping, dict):
        raise ValueError('Mapping must be a JSON object')
    return mapping


def list_hid():
    import hid
    for device in hid.enumerate():
        path = device['path']
        print(json.dumps({
            'vendor_id': f"0x{device['vendor_id']:04x}",
            'product_id': f"0x{device['product_id']:04x}",
            'usage_page': f"0x{device.get('usage_page', 0):04x}",
            'usage': device.get('usage'),
            'manufacturer': device.get('manufacturer_string'),
            'product': device.get('product_string'),
            'interface': device.get('interface_number'),
            'bus_type': device.get('bus_type'),
            'path_hex': path.hex(),
        }), flush=True)


def probe_hid(args):
    """Read descriptor and input reports without vendor writes or heartbeat."""
    import hid
    registry_id = getattr(args, 'registry_id', None)
    if registry_id is not None:
        if not 0 < registry_id < 2**64:
            raise ValueError('Registry ID must be a positive uint64 from hidutil list Devices')
        path = f'DevSrvsID:{registry_id}'.encode('ascii')
    else:
        path = bytes.fromhex(args.path)
    matches = [device for device in hid.enumerate() if device['path'] == path]
    if not matches and registry_id is None:
        raise ValueError('HID path is no longer present; run list-hid again')
    device = hid.device()
    try:
        device.open_path(path)
        print(json.dumps({'probe': 'read-only', 'path_hex': path.hex(),
                          'enumerated': bool(matches),
                          'collections': [{'usage_page': item.get('usage_page'),
                                           'usage': item.get('usage')} for item in matches]}), flush=True)
        try:
            descriptor = bytes(device.get_report_descriptor())
            print(json.dumps({'report_descriptor_hex': descriptor.hex()}), flush=True)
        except (AttributeError, OSError, NotImplementedError) as exc:
            print(json.dumps({'descriptor_error': str(exc),
                              'hint': 'Try upgrading hidapi, or capture ioreg -r -c IOHIDDevice -l -w 0'}), flush=True)
        print('Press/release each button, click the knob, and rotate both ways. '
              'Raw reports retain any report ID. Ctrl-C stops.', file=sys.stderr)
        started = time.monotonic()
        count = 0
        while time.monotonic() - started < args.seconds:
            raw = bytes(device.read(4096, 100))
            if raw:
                count += 1
                print(json.dumps({'elapsed_seconds': round(time.monotonic() - started, 3),
                                  'length': len(raw), 'raw_hex': raw.hex()}), flush=True)
        print(json.dumps({'reports_received': count, 'probe_complete': True}), flush=True)
        if not count:
            print('No reports received on this path. This does not establish a hardware fault; '
                  'another HID collection may carry the controls. Check Input Monitoring '
                  'permission for your terminal if opening or reading is blocked.', file=sys.stderr)
    finally:
        device.close()


async def scan(timeout):
    from bleak import BleakScanner
    # Keep BLEDevice objects: using one avoids a second implicit scan on connect.
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    return list(found.values())


def is_dial(device, advertisement):
    name = (advertisement.local_name or device.name or '').casefold()
    services = [uuid.casefold() for uuid in (advertisement.service_uuids or [])]
    return (SERVICE in services or 'ulanzi dial' in name or 'd100h' in name
            or 'dial_lite' in name or 'dial lite' in name)


async def run_bluetooth(args, dispatch):
    if not args.address:
        print('Scanning for D100H; put the dial in pairing mode if needed.', file=sys.stderr)
        found = await scan(args.timeout)
        candidates = [device for device, adv in found if is_dial(device, adv)]
        if len(candidates) != 1:
            for device, adv in found:
                print(json.dumps({'uuid': device.address,
                                  'name': adv.local_name or device.name}))
            raise ValueError(f'Found {len(candidates)} possible dials. Run scan, then ble DEVICE-UUID.')
        args.address = candidates[0]
        print(f'Connecting to {args.address.address}', file=sys.stderr)
    await run_ble(args, dispatch)


def run_hid(args, dispatch):
    """HIDAPI supplies macOS IOHIDManager transport; device framing is unverified."""
    import hid
    if not 0 <= args.report_id <= 255 or not 20 <= args.size <= 64:
        raise ValueError('Report ID must be 0..255; payload size must be 20..64')
    path = bytes.fromhex(args.path)
    matches = [device for device in hid.enumerate() if device['path'] == path]
    if not any(item.get('usage_page') in (0xfffc, 0xfffe) for item in matches):
        raise ValueError('Select a vendor interface on usage page 0xFFFC/0xFFFE from list-hid')
    device = hid.device()
    try:
        device.open_path(path)
        setting = dispatch.sleep_command(args, args.size)
        next_beat = 0
        while True:
            dispatch.check_sleep_reply()
            now = time.monotonic()
            if now >= next_beat:
                packet = bytes([args.report_id]) + heartbeat(args.size)
                if device.write(packet) != len(packet):
                    raise OSError('Short HID write; verify report ID and payload size')
                if setting is not None:
                    packet = bytes([args.report_id]) + setting
                    if device.write(packet) != len(packet):
                        raise OSError('Short sleep timer write; setting is unconfirmed')
                    setting = None
                next_beat = now + 1
            raw = bytes(device.read(4096, 100))
            if not raw:
                continue
            if args.report_id:
                if raw[0] != args.report_id:
                    continue
                raw = raw[1:]
            dispatch.receive(raw)
    finally:
        dispatch.held.clear()
        device.close()


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='mode', required=True)
    sub.add_parser('usb', help='List USB devices using macOS system_profiler')
    sub.add_parser('list-hid', help='List HID interfaces and their path_hex values')
    probe = sub.add_parser('probe-hid', help='Read-only HID descriptor and raw input test')
    probe_target = probe.add_mutually_exclusive_group(required=True)
    probe_target.add_argument('--path', help='Exact path_hex value from list-hid')
    probe_target.add_argument('--registry-id', type=lambda s: int(s, 0),
                              help='Device RegistryID from hidutil list, e.g. 0x10002aadd; opens directly')
    probe.add_argument('--seconds', type=float, default=30, help='Capture duration, default 30 seconds')
    scanner = sub.add_parser('scan', help='Print nearby BLE device UUIDs and names')
    scanner.add_argument('--timeout', type=float, default=15)
    ble = sub.add_parser('ble', help='Listen via BLE; omit UUID to discover a single dial')
    ble.add_argument('address', nargs='?', help='macOS Bluetooth UUID, not a MAC address')
    ble.add_argument('--timeout', type=float, default=15, help='Discovery seconds')
    wired = sub.add_parser('hid', help='Experimental vendor HID listener')
    wired.add_argument('--path', required=True, help='Exact path_hex value from list-hid')
    wired.add_argument('--report-id', required=True, type=lambda s: int(s, 0))
    wired.add_argument('--size', required=True, type=int, help='Payload size excluding report ID')
    for item in (ble, wired):
        item.add_argument('--map', type=Path, help='Optional JSON command mapping')
        add_sleep_options(item)
    return parser


async def print_scan(timeout):
    for device, adv in await scan(timeout):
        print(json.dumps({'uuid': device.address, 'name': adv.local_name or device.name,
                          'possible_d100h': is_dial(device, adv)}))


def main():
    parser = build_parser()
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.exit(2, 'This helper targets macOS. On Linux use d100h.py.\n')
    if getattr(args, 'timeout', 15) <= 0:
        parser.error('--timeout must be positive')
    if getattr(args, 'seconds', 30) <= 0:
        parser.error('--seconds must be positive')
    try:
        if args.mode == 'usb':
            subprocess.run(['system_profiler', 'SPUSBDataType'], check=True)
        elif args.mode == 'list-hid':
            list_hid()
        elif args.mode == 'probe-hid':
            probe_hid(args)
        elif args.mode == 'scan':
            asyncio.run(print_scan(args.timeout))
        else:
            dispatch = Dispatcher(load_mapping(args.map))
            if args.mode == 'ble':
                asyncio.run(run_bluetooth(args, dispatch))
            else:
                run_hid(args, dispatch)
    except KeyboardInterrupt:
        pass
    except ImportError as exc:
        parser.exit(1, f'{exc}. Install dependencies with: python3 -m pip install -r requirements-macos.txt\n')
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        permission = 'Bluetooth' if args.mode in ('scan', 'ble') else 'Input Monitoring'
        parser.exit(1, f'{exc}\nCheck System Settings > Privacy & Security > {permission} '
                    'and quit Ulanzi Studio before retrying.\n')


if __name__ == '__main__':
    main()
