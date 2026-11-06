"""Sleep timer protocol and acknowledgment behavior, without hardware."""
import argparse
import asyncio
import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import d100h
import d100h_macos


class SleepTimerTests(unittest.TestCase):
    def test_wire_format_and_range(self):
        # Documented two-hour command and read command, independently decoded
        # from Studio captures; codec itself has captured ciphertext tests.
        self.assertEqual(d100h.crypt(d100h.sleep_packet(7200)),
                         bytes.fromhex('01014202201c').ljust(20, b'\0'))
        self.assertEqual(d100h.crypt(d100h.sleep_packet()),
                         bytes.fromhex('01014201').ljust(20, b'\0'))
        for invalid in (-1, 65536, 1.5, True):
            with self.assertRaises(ValueError):
                d100h.sleep_packet(invalid)
        self.assertEqual(d100h.sleep_seconds('off'), 0)
        for invalid in ('-1', '65536', '1.5', 'later'):
            with self.assertRaises(argparse.ArgumentTypeError):
                d100h.sleep_seconds(invalid)
        args = d100h_macos.build_parser().parse_args(['ble', '--sleep', 'off'])
        self.assertEqual(args.sleep, 0)

    def test_read_does_not_acknowledge_write_and_mismatch_fails(self):
        dispatch = d100h.Dispatcher()
        dispatch.sleep_command(SimpleNamespace(sleep=7200))
        with contextlib.redirect_stdout(io.StringIO()):
            dispatch.receive(d100h.crypt(bytes.fromhex('01014211201c').ljust(20, b'\0'), True))
            self.assertIsNotNone(dispatch.pending_sleep)
            dispatch.receive(d100h.crypt(bytes.fromhex('01014212100e').ljust(20, b'\0'), True))
        with self.assertRaisesRegex(RuntimeError, 'returned 3600s, requested 7200s'):
            dispatch.check_sleep_reply()

    def test_missing_reply_is_unconfirmed(self):
        dispatch = d100h.Dispatcher()
        with patch.object(d100h.time, 'monotonic', return_value=0):
            dispatch.sleep_command(SimpleNamespace(read_sleep=True))
        with patch.object(d100h.time, 'monotonic', return_value=6):
            with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                dispatch.check_sleep_reply()

    def test_ble_sends_heartbeat_then_setting_and_waits_for_reply(self):
        client = SimpleNamespace(is_connected=True)
        client.services = SimpleNamespace(get_service=lambda _: SimpleNamespace(get_characteristic=lambda _: object()))
        writes = []

        class Client:
            async def __aenter__(self):
                return client
            async def __aexit__(self, *_):
                pass

        async def start_notify(_uuid, callback):
            client.callback = callback

        async def write(_uuid, packet, response):
            self.assertFalse(response)
            writes.append(d100h.crypt(packet))
            if writes[-1][:4] == bytes.fromhex('01014202'):
                client.callback(None, d100h.crypt(bytes.fromhex('010142120000').ljust(20, b'\0'), True))

        async def stop_notify(_uuid):
            pass

        async def sleep(_seconds):
            client.is_connected = False

        client.start_notify = start_notify
        client.stop_notify = stop_notify
        client.write_gatt_char = write
        dispatch = d100h.Dispatcher()
        with patch.dict('sys.modules', {'bleak': SimpleNamespace(BleakClient=lambda _: Client())}), \
             patch.object(d100h.asyncio, 'sleep', sleep), \
             contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(d100h.run_ble(SimpleNamespace(address='UUID', sleep=0), dispatch))
        self.assertEqual([wire[:4].hex() for wire in writes], ['06012300', '01014202'])
        self.assertIsNone(dispatch.pending_sleep)


if __name__ == '__main__':
    unittest.main()
