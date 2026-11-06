"""Captured BLE vectors and HID prefix cross-checks; no hardware required."""
import contextlib
import io
import json
import unittest
from d100h import crypt, heartbeat, Dispatcher


class ProtocolTests(unittest.TestCase):
    def test_captured_vectors(self):
        vectors = [
            ('8b100101', '8e8f5e16ec67281e3890c499a360aaad00000000'),
            ('8b100100', '028db3660224d0c73890c499a360aaad00000000'),
            ('0601230001', '24569ef228e145a13890c499a360aaad00000000'),
            ('0601231001', '21f45eb71c3587ab3890c499a360aaad00000000'),
        ]
        for prefix, ciphertext in vectors:
            plain = bytes.fromhex(prefix).ljust(20, b'\0')
            wire = bytes.fromhex(ciphertext)
            self.assertEqual(crypt(wire), plain)
            self.assertEqual(crypt(plain, True), wire)
        self.assertEqual(heartbeat().hex(), vectors[2][1])

    def test_hid_codes_match_independent_capture(self):
        # ftl/hamdial event.go, including physical button 7 = lower-right.
        for prefix, code in [
            ('8b100201', '22d73cfa751ffbc7'),
            ('8b100301', '983ae16d833a42a4'),
            ('8b100401', '3d2e482ac73d9006'),
            ('8b100501', 'e6ae57c883933971'),
            ('8b100601', '2e426bb625bb36dd'),
            ('8b100801', 'ff79b4421864a3f1'),
            ('8b100701', '838cc9ca145c72ed'),
            ('8b100a01', '3edd5f9e876af42c'),
            ('8b100901', 'b06be536dbe92e9d'),
        ]:
            self.assertEqual(crypt(bytes.fromhex(code)), bytes.fromhex(prefix).ljust(8, b'\0'))

    def test_dispatch_and_modifiers(self):
        dispatch = Dispatcher()
        out = io.StringIO()
        # Lower-left held, free CW, held-knob CCW, knob click pair, side release.
        prefixes = ['8b100601', '8b100a01', '8b100b01', '8b100701', '8b100700', '8b100600']
        with contextlib.redirect_stdout(out):
            for prefix in prefixes:
                dispatch.receive(crypt(bytes.fromhex(prefix).ljust(20, b'\0'), True))
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([e['event'] for e in events],
                         ['lower_left.down', 'rotate.cw', 'held_rotate.ccw', 'knob.click', 'lower_left.up'])
        self.assertEqual(events[1]['held_buttons'], ['lower_left'])
        self.assertEqual(dispatch.held, set())


if __name__ == '__main__':
    unittest.main()
