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
- **macOS wired USB confirmed by a user on D100H `FFF1:00DE`**, including
  button events, knob clicks, free/held rotation, and example command mappings.
- Linux USB, this project's BLE listener, ESP32 integration, and sleep-timer
  operations have not received live validation in this project.
- BLE mappings were captured by the referenced project on firmware **1.3.8.1**.
  Verify them on other revisions.
- Confirmed wired framing: vendor page `0xFFFE`, **report ID `0`**, **64-byte
  input/output payloads**. Other revisions still need descriptor checks.
- See [wired USB evidence](tests/fixtures/d100h_wired_usb.json). The supplied event
  log covers IDs `02`, `04`–`0C`; top-left/right were not separately logged.
  Firmware and macOS versions were not supplied.

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

For the confirmed **wired D100H (`FFF1:00DE`)**, keep USB connected and quit
Ulanzi Studio. Select the entry with `usage_page: "0xfffe"` from `list-hid`,
then copy its current `path_hex`:

```sh
D100H_PATH=PASTE_VENDOR_PATH_HEX_HERE

# Print decoded button/knob events.
python3 d100h_macos.py hid --path "$D100H_PATH" --report-id 0 --size 64
```

Stop with Ctrl-C, then enable the macOS example actions:

```sh
python3 d100h_macos.py hid --path "$D100H_PATH" --report-id 0 --size 64 \
  --map mappings.macos.example.json
```

The user confirmed the listener and mapped actions work. The standard mouse
collection on page `0x0001` is a different interface; its five-byte zero reports
do not contain vendor control events. Use the **Devices** section of
`hidutil list` to locate page `65534` (`0xFFFE`) if necessary. Paths and registry
IDs change after reconnecting; examples are not permanent device identifiers.

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

To test the USB HID input without sending vendor commands, connect the cable,
quit Ulanzi Studio, and choose the current `path_hex` from `list-hid`:

```sh
python3 d100h_macos.py probe-hid --path YOUR_PATH_HEX --seconds 30
```

If `hidutil list` shows the vendor device (usage page `65534` = `0xFFFE`) but
HIDAPI does not enumerate it, select its **Devices** registry ID directly:

```sh
# Example ID; use the current RegistryID from your Mac's Devices section.
python3 d100h_macos.py probe-hid --registry-id 0x10002aadd --seconds 30
```

This resolves HIDAPI's `DevSrvsID:<decimal registry ID>` path and attempts a
read-only open even when enumeration omits it. A failed open still requires
checking permissions or ownership by another application. Registry IDs change
when devices reconnect; do not use a Services section ID in their place.

During capture, press and release each button, click the knob, rotate both ways,
and rotate while holding the knob. The probe prints the report descriptor when
available and timestamped raw input reports, preserving any report ID. It accepts
standard mouse/keyboard collections as well as vendor collections, and handles
multiple collections sharing a path. It sends no heartbeat, output reports, or
feature writes, so factory actions may still occur. Raw input does not by itself
provide the vendor-mode control mapping.

A zero-report result does not prove a hardware fault. The confirmed vendor
interface was silent during the read-only probe and began reporting controls
when the active `hid` listener sent heartbeats. A mouse collection may also
not carry those controls. If access fails, check **Privacy & Security → Input
Monitoring** for your terminal. For a descriptor error, try upgrading `hidapi` or
capture `ioreg -r -c IOHIDDevice -l -w 0`. Paths may change after reconnecting the
USB cable; enumerate again if the path is no longer found. Share the descriptor
and raw reports to determine the vendor report ID and size for your revision.

Only vendor usage pages `0xFFFC` and `0xFFFE` are accepted by the active listener.
The confirmed USB descriptor has 64-byte Input and Output reports, with **no
Report ID item**. HIDAPI writes therefore use a leading zero plus the encrypted
64-byte payload; reads contain only the 64-byte payload. This differs from the
Bluetooth HID report `0xFB` used by another implementation.

For other revisions, obtain the descriptor before selecting report sizes and IDs.
The helper does not automatically reconnect.

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

## Change the sleep timer

Both listeners accept `--sleep SECONDS` (or `--sleep off`) and `--read-sleep`.
The command runs once after connecting; the script then keeps listening for
button/knob events. Press Ctrl-C to disconnect after the timer reply appears.

On macOS, omit the UUID for automatic discovery or supply it after `ble`:

```sh
# Read the configured timer.
python3 d100h_macos.py ble --read-sleep
# Set two hours (7200 seconds).
python3 d100h_macos.py ble --sleep 7200
# Disable automatic sleep.
python3 d100h_macos.py ble --sleep off
```

On Linux:

```sh
python3 d100h.py ble AA:BB:CC:DD:EE:FF --read-sleep
python3 d100h.py ble AA:BB:CC:DD:EE:FF --sleep 7200
```

For the confirmed wired model's configuration, these are the sleep commands
(**sleep read/write is still awaiting live verification**):

```sh
python3 d100h_macos.py hid --path "$D100H_PATH" --report-id 0 --size 64 --read-sleep
python3 d100h_macos.py hid --path "$D100H_PATH" --report-id 0 --size 64 --sleep 7200
python3 d100h_macos.py hid --path "$D100H_PATH" --report-id 0 --size 64 --sleep off
```

The same flags work with the Linux `hid` mode; keep its required path, report ID
and payload size arguments. They can also be combined with `--map`.
Quit Ulanzi Studio before changing the device settings.

Values are whole seconds from **0 to 65535**. `0`/`off` disables the timer; Studio's
observed presets are `3600`, `7200`, `10800`, and `14400` (one to four hours).
Other durations may be accepted, but their actual sleep timing is unverified.

A reply prints, for example:

```json
{"sleep_timer_seconds": 7200, "disabled": false, "write_response": true}
```

`write_response: true` is the device's setting acknowledgment; a read response
has `false`. A missing acknowledgment after five seconds or a reply with a
different value is reported as an error. A successful write alone is not treated
as confirmation. Settings retention after a power cycle was independently observed
on firmware 1.3.8.1 by the independent BLE capture project. Sleep operations in
these scripts are fixture-tested; they were not part of the user's wired
validation.
The accepted duration does not establish the exact conditions for entering sleep.

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
# Values confirmed on macOS for wired FFF1:00DE; Linux live testing is pending.
python3 d100h.py hid /dev/hidrawN --report-id 0 --size 64
```

`--size` excludes the report ID. The HID transport encrypts the selected payload
as the supplied driver does. The independently documented Bluetooth HID identity
`FFF1:0082` and report `FB` belong to the Bluetooth HID route. The user-confirmed
wired device is `FFF1:00DE`, page `FFFE`, unnumbered 64-byte reports.

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
