# Ulanzi D100H Protocol

Unofficial D100H protocol notes, Linux/macOS event listeners with command mappings, and
a portable C++ decoder for ESP32 integration.

The supplied Ulanzi Studio 3.4.8 macOS package was statically inspected without
running its installer. Its KEHWIN library uses the same TEA key as independently
captured D100H BLE traffic. See [analysis findings](analysis/FINDINGS.txt) for the
package checksum, binary offsets, sources, and limitations.

## Status

- Codec checked against captured plaintext/ciphertext pairs and independent HID
  event codes.
- Python event dispatch tested, including held buttons and one-shot knob clicks.
- C++11 decoder compiled and checked against captured packets on a Linux host.
- No live D100H or ESP32 validation performed here.
- BLE mappings were captured by the referenced project on firmware **1.3.8.1**.
  Verify them on other revisions.
- Wired USB support is experimental: inspect the actual HID descriptor first.
  USB report IDs, sizes, and identity must not be inferred from Bluetooth IDs.

## Linux BLE listener

Use Python 3.10+ and a working BlueZ Bluetooth setup:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Pair the controller through the operating system's Bluetooth settings, close
Ulanzi Studio, and replace the address below with your controller's address.
For the dual-mode model, disconnect USB from the computer while testing BLE;
Ulanzi advertises wired priority.

```sh
.venv/bin/python d100h.py ble AA:BB:CC:DD:EE:FF
```

The listener subscribes to the vendor channel, sends an immediate heartbeat and
then one per second, and prints JSON events. On the independently tested firmware,
heartbeats enable vendor events and suppress factory keyboard/media actions.
Standalone behavior resumes approximately three seconds after heartbeats stop.
The listener does not automatically reconnect.

## macOS helper

Install the macOS dependencies in a virtual environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-macos.txt
```

Inspect USB and HID devices (the equivalents of Linux `lsusb` and `list-hid`):

```sh
python3 d100h_macos.py usb
python3 d100h_macos.py list-hid
```

`list-hid` prints one JSON record per interface, including vendor/product IDs,
usage page, and an exact `path_hex` selection value. It does not send commands.

For Bluetooth, pair in macOS settings, quit Ulanzi Studio, unplug USB from the
computer, and allow Bluetooth access for Terminal or your IDE when prompted.
The helper can automatically discover a single advertised dial:

```sh
python3 d100h_macos.py ble
```

If discovery finds zero or multiple candidates, put the dial into pairing mode
and select its UUID explicitly:

```sh
python3 d100h_macos.py scan
python3 d100h_macos.py ble YOUR-DEVICE-UUID
python3 d100h_macos.py ble YOUR-DEVICE-UUID --map mappings.macos.example.json
```

macOS uses a Bluetooth UUID, not the controller's Bluetooth MAC address. The
macOS mapping example opens Calculator/Music and controls system volume using
`open` and `osascript`. Edit it to assign other actions. Run without `--map`
to only print events. Bluetooth permission can be changed in **System Settings →
Privacy & Security → Bluetooth**.

The helper also offers experimental HIDAPI access for the wired model. After
confirming the vendor interface and its report descriptor, use:

```sh
# Example report values only; verify for your hardware first.
python3 d100h_macos.py hid --path PATH_HEX_FROM_LIST_HID --report-id 0xfb --size 64
```

Only vendor usage pages `0xFFFC` and `0xFFFE` are accepted. Enumeration does not
supply a report size; obtain that from the descriptor or a capture before writing.
This transport and the macOS helper have been tested with mocked I/O on Linux,
not on a live Mac/D100H. The helper does not automatically reconnect.

## Map controls to commands

Edit `mappings.example.json` and enable it explicitly:

```sh
.venv/bin/python d100h.py ble AA:BB:CC:DD:EE:FF --map mappings.example.json
```

Mappings associate an event name with a program and argument array:

```json
{
  "top_left.down": ["playerctl", "play-pause"],
  "rotate.cw": ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1%+"]
}
```

The sample requires `playerctl` and `wpctl`. Substitute your own commands as
needed. Commands run without a shell; omit `--map` to only observe events.
Top/side buttons have `.down` and `.up` events. Output includes `held_buttons`,
which applications can use for button-modified rotation.

With the Ulanzi logo upright:

| Decrypted ID | Control | Listener event |
|---|---|---|
| `01` | Top-left | `top_left.down` / `top_left.up` |
| `02` | Top-middle | `top_middle.down` / `top_middle.up` |
| `03` | Top-right | `top_right.down` / `top_right.up` |
| `04` | Upper-left side | `upper_left.down` / `upper_left.up` |
| `05` | Upper-right side | `upper_right.down` / `upper_right.up` |
| `06` | Lower-left side | `lower_left.down` / `lower_left.up` |
| `08` | Lower-right side | `lower_right.down` / `lower_right.up` |
| `07` | Knob click | `knob.click` |
| `09` / `0A` | Counterclockwise / clockwise | `rotate.ccw` / `rotate.cw` |
| `0B` / `0C` | Turn while pressing knob | `held_rotate.ccw` / `held_rotate.cw` |

Knob clicks are synthetic down/up pairs delivered on physical release in the
captured firmware. The listener emits one `knob.click`. These events cannot
measure physical knob hold duration. Held rotation has separate IDs.

## BLE framing

| Role | UUID |
|---|---|
| Service | `00001971-0000-1000-8000-00805f9b34fb` |
| Write without response | `0000fd01-0000-1000-8000-00805f9b34fb` |
| Notify | `0000fd00-0000-1000-8000-00805f9b34fb` |

BLE vendor values are 20 bytes. TEA encrypts each complete eight-byte block using
32 rounds and little-endian words; the final four bytes remain unchanged.
The fixed key bytes are `cabaa5ca6d8a2abcba9e5acaca8bb89b`.

The encrypted BLE heartbeat is:

```text
24569ef228e145a13890c499a360aaad00000000
```

Decrypted control messages start `8b 10 ID STATE`, where `01` means down/step and
`00` means up. Discover characteristics by UUID rather than hard-coded ATT handles.

## Experimental Linux HID transport

Inspect the controller's identity and report descriptor without sending commands:

```sh
lsusb
python3 d100h.py list-hid
```

After confirming the vendor interface, report ID, payload size, and access
permissions, use:

```sh
# Example values only; verify against your actual descriptor.
python3 d100h.py hid /dev/hidrawN --report-id 0xfb --size 64
```

`--size` excludes the report ID. The HID transport encrypts the selected payload
as the supplied driver does. The independently documented Bluetooth HID identity
`FFF1:0082` and report `FB` do not establish your wired revision's USB identity.

## ESP32

Use a BLE-capable board as a central/GATT client. Subscribe to notifications,
send the heartbeat every second, and feed notifications into the decoder:

- [Portable C++ header](esp32/d100h_protocol.h)
- [Integration instructions and dispatch sketch](esp32/INTEGRATION.txt)

This is a codec and integration guide, not a complete firmware project.

## Tests

```sh
python3 -m unittest discover -v
```

The downloaded installer, extracted application, temporary tools, and third-party
reference checkouts are excluded from version control. Detailed disassembly
mentioned in the findings was retained in the original analysis workspace only.

## Sources

- [Ulanzi D100H product page](https://www.ulanzi.com/products/d100h-dial-creative-controller-i003)
- [kriomant/ulanzi-d100h](https://github.com/kriomant/ulanzi-d100h): BLE captures, decoded events, and packet vectors (MIT).
- [ftl/hamdial](https://github.com/ftl/hamdial): independent Linux HID captures and implementation (MIT).
- [Espressif GATT client example](https://github.com/espressif/esp-idf/tree/master/examples/bluetooth/bluedroid/ble/gatt_client)

This project is unofficial and is not affiliated with Ulanzi or KEHWIN.
