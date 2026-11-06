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
from d100h import Dispatcher, crypt


class MacTests(unittest.TestCase):
    def test_probe_registry_id_opens_vendor_device_missing_from_enumeration(self):
        dev = Mock()
        dev.get_report_descriptor.return_value = [0x06, 0xfe, 0xff]
        hid = SimpleNamespace(enumerate=lambda: [], device=lambda: dev)
        args = mac.build_parser().parse_args(['probe-hid', '--registry-id', '0x10002aadd'])
        out = io.StringIO()
        with patch.dict('sys.modules', {'hid': hid}), \
             patch.object(mac.time, 'monotonic', side_effect=[0, 31]), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            mac.probe_hid(args)
        dev.open_path.assert_called_once_with(b'DevSrvsID:4295142109')
        self.assertFalse(json.loads(out.getvalue().splitlines()[0])['enumerated'])
        self.assertIn('06feff', out.getvalue())
        dev.write.assert_not_called()
        dev.close.assert_called_once()

    def test_read_only_probe_handles_duplicate_mouse_collections(self):
        path = b'DevSrvsID:4295142103'
        info = [{'path': path, 'usage_page': 1, 'usage': usage} for usage in (2, 1)]
        dev = Mock()
        dev.get_report_descriptor.return_value = [0x05, 0x01, 0x09, 0x02]
        dev.read.return_value = [0x01, 0x00, 0x02]
        hid = SimpleNamespace(enumerate=lambda: info, device=lambda: dev)
        out = io.StringIO()
        with patch.dict('sys.modules', {'hid': hid}), \
             patch.object(mac.time, 'monotonic', side_effect=[0, 0, 0.1, 31]), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            mac.probe_hid(argparse.Namespace(path=path.hex(), seconds=30))
        records = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(len(records[0]['collections']), 2)
        self.assertEqual(records[1]['report_descriptor_hex'], '05010902')
        self.assertEqual(records[2]['raw_hex'], '010002')
        self.assertEqual(records[3]['reports_received'], 1)
        dev.write.assert_not_called()
        dev.send_feature_report.assert_not_called()
        dev.close.assert_called_once()

    def test_probe_keeps_reading_when_descriptor_unavailable(self):
        dev = Mock()
        dev.get_report_descriptor.side_effect = OSError('unsupported')
        dev.read.return_value = []
        hid = SimpleNamespace(enumerate=lambda: [{'path': b'path'}], device=lambda: dev)
        out = io.StringIO()
        with patch.dict('sys.modules', {'hid': hid}), \
             patch.object(mac.time, 'monotonic', side_effect=[0, 0, 31]), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            mac.probe_hid(argparse.Namespace(path=b'path'.hex(), seconds=30))
        records = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(records[1]['descriptor_error'], 'unsupported')
        self.assertEqual(records[-1]['reports_received'], 0)
        dev.read.assert_called_once()
        dev.write.assert_not_called()

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
        reply = b'\xfb' + crypt(bytes.fromhex('01014212201c').ljust(64, b'\0'), True)
        dev.read.side_effect = [list(reply), list(wire), KeyboardInterrupt()]
        dev.write.side_effect = lambda packet: len(packet)
        hid = SimpleNamespace(enumerate=lambda: [info], device=lambda: dev)
        out = io.StringIO()
        dispatch = Dispatcher()
        with patch.dict('sys.modules', {'hid': hid}), contextlib.redirect_stdout(out):
            mac.list_hid()
            with self.assertRaises(KeyboardInterrupt):
                mac.run_hid(argparse.Namespace(path=path.hex(), report_id=0xfb, size=64, sleep=7200), dispatch)
        records = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(records[0]['path_hex'], path.hex())
        self.assertEqual(records[1]['sleep_timer_seconds'], 7200)
        self.assertEqual(records[2]['event'], 'top_left.down')
        self.assertEqual(dev.write.call_args_list[0].args[0][:9].hex(), 'fb24569ef228e145a1')
        self.assertEqual(len(dev.write.call_args.args[0]), 65)
        self.assertEqual(crypt(dev.write.call_args.args[0][1:])[:6].hex(), '01014202201c')
        self.assertIsNone(dispatch.pending_sleep)
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
