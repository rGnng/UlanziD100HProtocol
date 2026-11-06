"""macOS helper behavior with mocked transport; runnable on Linux."""
import argparse
import asyncio
import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import d100h_macos as mac
from d100h import Dispatcher


class MacTests(unittest.TestCase):
    def test_discover_single_dial_passes_device_object(self):
        device = SimpleNamespace(address='macOS-UUID', name='Ulanzi Dial')
        adv = SimpleNamespace(local_name=None, service_uuids=[])
        args = argparse.Namespace(address=None, timeout=1)
        dispatch = Dispatcher()
        with patch.object(mac, 'scan', AsyncMock(return_value=[(device, adv)])), \
             patch.object(mac, 'run_ble', AsyncMock()) as listener, \
             contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(mac.run_bluetooth(args, dispatch))
        self.assertIs(args.address, device)
        listener.assert_awaited_once_with(args, dispatch)

    def test_ambiguous_discovery_never_opens_dial(self):
        devices = [(SimpleNamespace(address=str(i), name='D100H'),
                    SimpleNamespace(local_name=None, service_uuids=[])) for i in range(2)]
        with patch.object(mac, 'scan', AsyncMock(return_value=devices)), \
             patch.object(mac, 'run_ble', AsyncMock()) as listener, \
             contextlib.redirect_stderr(io.StringIO()), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'Found 2'):
                asyncio.run(mac.run_bluetooth(argparse.Namespace(address=None, timeout=1), Dispatcher()))
        listener.assert_not_awaited()

    def test_hid_listing_and_transport(self):
        path = b'IOService:/vendor-device'
        info = {'path': path, 'vendor_id': 0xfff1, 'product_id': 0xdd, 'usage_page': 0xfffc}
        dev = Mock()
        # Captured top-left down, with numbered HID report prefix.
        wire = bytes.fromhex('fb8e8f5e16ec67281e3890c499a360aaad00000000')
        dev.read.side_effect = [list(wire), KeyboardInterrupt()]
        dev.write.side_effect = lambda packet: len(packet)
        hid = SimpleNamespace(enumerate=lambda: [info], device=lambda: dev)
        out = io.StringIO()
        dispatch = Dispatcher()
        with patch.dict('sys.modules', {'hid': hid}), contextlib.redirect_stdout(out):
            mac.list_hid()
            with self.assertRaises(KeyboardInterrupt):
                mac.run_hid(argparse.Namespace(path=path.hex(), report_id=0xfb, size=64), dispatch)
        records = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(records[0]['path_hex'], path.hex())
        self.assertEqual(records[1]['event'], 'top_left.down')
        self.assertEqual(dev.write.call_args.args[0][:9].hex(), 'fb24569ef228e145a1')
        self.assertEqual(len(dev.write.call_args.args[0]), 65)
        dev.open_path.assert_called_once_with(path)
        dev.close.assert_called_once()
        self.assertFalse(dispatch.held)

    def test_hid_rejects_nonvendor_interface_before_open(self):
        hid = SimpleNamespace(enumerate=lambda: [{'path': b'keyboard', 'usage_page': 1}], device=Mock())
        with patch.dict('sys.modules', {'hid': hid}):
            with self.assertRaisesRegex(ValueError, 'vendor interface'):
                mac.run_hid(argparse.Namespace(path=b'keyboard'.hex(), report_id=0xfb, size=64), Dispatcher())
        hid.device.assert_not_called()


if __name__ == '__main__':
    unittest.main()
