# TINK Breadboard Remote (Arduino → Pi over USB)

A 6-button controller on a breadboard. An ATmega32U4 Arduino (Micro /
SparkFun Pro Micro / Leonardo) presents itself as a **USB keyboard** to the
Raspberry Pi — so the media player works with it out of the box. No
software changes on the Pi, no drivers, no UDP/network needed.

Sketch: [`tink_remote/tink_remote.ino`](tink_remote/tink_remote.ino)

## Parts

- 1× Arduino Micro / SparkFun Pro Micro / Leonardo (must be ATmega32U4)
- 6× tactile push buttons
- Breadboard + jumper wires
- Micro-USB/USB-B cable (also powers + flashes the board)

## Breadboard diagram

Buttons are **active-low**: one leg goes to the shared GND rail, the other
leg to the numbered Arduino pin. `INPUT_PULLUP` in the sketch means **no
resistors needed** — the GPIO is internally pulled high until you press.

```
                     ┌──────────────────────┐
                     │   Arduino Pro Micro  │
                     │  (USB side to the Pi)│
                     │                      │
                     │ D2  D3  D4  D5  D6 D7│  ... GND
                     └──┼───┼───┼───┼───┼─┼┘
                        │   │   │   │   │  │
                        │   │   │   │   │  │
   ┌────────── breadboard row per button ─────────────┐
   │                                                  │
   │   enter   back   left   right    up   down       │
   │   ┌─┐     ┌─┐    ┌─┐    ┌─┐      ┌─┐   ┌─┐        │
   │   │ ├─────│ ├────│ ├────│ ├──────│ ├───│ ├        │
   │   └┬┘     └┬┘    └┬┘    └┬┘      └┬┘   └┬┘        │
   │    │      │      │      │        │     │         │
   └────┼──────┼──────┼──────┼────────┼─────┼─────────┘
        │      │      │      │        │     │
   ═════╧══════╧══════╧══════╧════════╧═════╧═════  ← GND rail (blue −)
   ═══════════════════════════════════════════════
                       │
                     Arduino GND
```

One tactile button straddles the breadboard's center gap; the two legs on
the same side of the gap are connected internally, so orient each button so
its two used legs are on different halves (one to the pin, one to GND).
If a press registers twice/never, rotate the button 90°.

### Pin map (Pro Micro numbers)

| Button | Arduino pin | Sends | Player behavior |
| ------ | ----------- | ----- | --------------- |
| enter  | D2          | Return | home/library: play · **player: pause/play** |
| back   | D3          | Esc    | previous screen · **player: exit video** |
| left   | D4          | ←      | move selection · seek −10 s |
| right  | D5          | →      | move selection · seek +10 s |
| up     | D6          | ↑      | move selection by row |
| down   | D7          | ↓      | move selection by row |
| GND    | GND         | —      | shared rail for all buttons |

Notes:

- D0/D1 (RX/TX) are left free for serial debugging.
- Onboard LED flashes on each press so you can verify wiring without the Pi.
- Holding a button auto-repeats at the OS level (hold left/right to keep
  skipping 10 s at a time).

## Flash the Arduino

Arduino IDE: open the `.ino`, select your board (Micro / Pro Micro 5V 16MHz /
Leonardo), pick the USB port, Upload.

Or with `arduino-cli`:

```bash
# Pro Micro example
arduino-cli compile --fqbn sparkfun:avr:promicro5 tink_remote
arduino-cli upload --fqbn sparkfun:avr:promicro5 -p /dev/ttyACM0 tink_remote
```

## Verify on the Raspberry Pi

```bash
# The board appears as a HID keyboard:
lsusb | grep -i arduino
# Press buttons and watch events:
sudo evtest        # pick the Arduino event device
# Or install and check:
sudo apt install evtest
```

Then run the player normally — the Pi's Wayland/X session delivers the
key events to the app; SSH is not involved:

```bash
python3 media_player.py --display dpi
```

Behavior while playing: `enter` pauses/plays, `back` exits to the library,
`left`/`right` seek ±10 s (hold to repeat). On the home screen the arrows
move the `external disk` / `computer` selection and `enter` opens it.

## Optional: UDP variant

If you'd rather use an ESP32/ESP8266 (Wi-Fi, no USB cable), have it send one
UDP datagram per press to the Pi and run the player with
`--control-port 5005` — see the "Remote control" section in the project
README (`../README.md`).
