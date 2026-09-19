// TINK projector remote — Arduino USB HID keyboard
// ================================================================
// Loads onto a USB-HID-capable Arduino (ATmega32U4): Arduino Micro,
// SparkFun Pro Micro, or Leonardo. When plugged into the Raspberry Pi's
// USB port, the Pi mounts it as a keyboard — the media player's key
// handler (ui.py) reacts natively. No changes on the Pi side.
//
// Key map (matches ui.MediaPlayerWindow._dispatch):
//   enter -> Return   : home/library activate; PAUSE/PLAY while playing
//   back  -> Escape   : previous screen; exit the video while playing
//   left  -> Left     : move selection; SEEK -10 s while playing
//   right -> Right    : move selection; SEEK +10 s while playing
//   up    -> Up       : move selection (library rows)
//   down  -> Down     : move selection (library rows)
//
// Wiring: each button bridges its Arduino pin to the shared GND rail.
// INPUT_PULLUP means no external resistors are needed.
//
// Holding a button holds the key down, so the Pi's key auto-repeat gives
// you continuous 10-second seeks while holding left/right.

#include <Keyboard.h>

// Boards without USB HID (UNO, Nano, Mega) cannot run Keyboard.h.
#ifndef USBCON
#error "This sketch needs an ATmega32U4 board: Arduino Micro, Pro Micro, or Leonardo."
#endif

#if defined(ARDUINO_ARCH_MEGAAVR)
#error "For Arduino Nano Every / UNO R4 use the built-in Keyboard.h with TinyUSB, or use a Pro Micro."
#endif

struct Button {
  uint8_t pin;
  char key;
  const char* label;
};

// Pro Micro friendly pins: D2..D7 (avoid D0/D1 = serial RX/TX).
Button buttons[] = {
  { 2, KEY_RETURN,      "enter" },
  { 3, KEY_ESC,         "back"  },
  { 4, KEY_LEFT_ARROW,  "left"  },
  { 5, KEY_RIGHT_ARROW, "right" },
  { 6, KEY_UP_ARROW,    "up"    },
  { 7, KEY_DOWN_ARROW,  "down"  },
};

const uint8_t BUTTON_COUNT = sizeof(buttons) / sizeof(buttons[0]);
const unsigned long DEBOUNCE_MS = 30;
const unsigned long LED_PULSE_MS = 40;

bool stablePressed[BUTTON_COUNT];   // debounced state
unsigned long lastEdgeMs[BUTTON_COUNT];
unsigned long ledOffAtMs = 0;

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Keyboard.begin();
  for (uint8_t i = 0; i < BUTTON_COUNT; i++) {
    pinMode(buttons[i].pin, INPUT_PULLUP);
    stablePressed[i] = false;
    lastEdgeMs[i] = 0;
  }
}

void loop() {
  const unsigned long now = millis();

  for (uint8_t i = 0; i < BUTTON_COUNT; i++) {
    const bool raw = digitalRead(buttons[i].pin) == LOW; // active-low
    if (raw != stablePressed[i] && (now - lastEdgeMs[i]) >= DEBOUNCE_MS) {
      stablePressed[i] = raw;
      lastEdgeMs[i] = now;
      if (raw) {
        Keyboard.press(buttons[i].key);
        digitalWrite(LED_BUILTIN, HIGH);   // flash on every press
        ledOffAtMs = now + LED_PULSE_MS;
      } else {
        Keyboard.release(buttons[i].key);
      }
    }
  }

  if (ledOffAtMs != 0 && now >= ledOffAtMs) {
    digitalWrite(LED_BUILTIN, LOW);
    ledOffAtMs = 0;
  }
}
