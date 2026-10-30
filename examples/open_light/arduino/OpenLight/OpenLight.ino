// Open Light Protocol v1: reference firmware for Arduino-compatible boards
// (RP2040 with the Earle Philhower core, ESP32 with core 3.x, Teensy, SAMD,
// AVR...). The protocol is in FreeCCR's docs/open-light-protocol.md.
// MIT licensed (see the end), so it can go into your own light's firmware.
//
// Each channel's pin drives that colour's LED driver (a logic-level MOSFET or
// a constant-current driver's PWM/dim input, never LEDs straight off a pin).
// SHUTTER_PIN and FOCUS_PIN drive optocouplers across the camera's remote
// release contacts. Use a board and core that can run PWM fast (20 kHz here):
// at the few hundred Hz an Uno gives, a fast shutter speed catches the LEDs
// part-way through a cycle and frames come out uneven.
//
// Not compiled by FreeCCR's tests (the MicroPython version is tested); it
// uses only the core Arduino API plus each core's own PWM-frequency call.

const char *NAME = "DIY Open Light (Arduino)";
const char *KEYS[5] = {"R", "G", "B", "W", "IR"};
const int PINS[5] = {16, 17, 18, 19, -1};   // -1: no such channel
const int SHUTTER_PIN = 20;                 // -1 if not fitted
const int FOCUS_PIN = 21;                   // -1 if not fitted
const unsigned long STATUS_EVERY_MS = 0;    // unprompted STATUS lines (0 = off)
const int MAX_LINE = 512;

char line[MAX_LINE + 1];
int lineLen = 0;
unsigned long lastStatus = 0;

void setLevel(int i, int v) {
  if (PINS[i] >= 0) analogWrite(PINS[i], v);
}

void sendHello() {
  Serial.print("OLP 1 CH=");
  bool first = true;
  for (int i = 0; i < 5; i++) {
    if (PINS[i] < 0) continue;
    if (!first) Serial.print(",");
    Serial.print(KEYS[i]);
    first = false;
  }
  Serial.print(" SHUTTER=");
  Serial.print(SHUTTER_PIN >= 0 ? 1 : 0);
  Serial.print(" FOCUS=");
  Serial.print(FOCUS_PIN >= 0 ? 1 : 0);
  Serial.print(" NAME=");
  Serial.println(NAME);
}

void sendStatus() {
  Serial.println("STATUS");   // add " TEMP=<C>" / " VBUS=<V>" if the board measures them
}

int channelIndex(const char *key) {
  for (int i = 0; i < 5; i++) {
    if (strcasecmp(key, KEYS[i]) == 0) return i;
  }
  return -1;
}

void handle(char *cmd) {
  char *tok = strtok(cmd, " ");
  if (!tok) return;
  if (strcasecmp(tok, "HELLO") == 0) { sendHello(); return; }
  if (strcasecmp(tok, "STATUS") == 0) { sendStatus(); return; }
  if (strcasecmp(tok, "OFF") == 0) {
    for (int i = 0; i < 5; i++) setLevel(i, 0);
    Serial.println("OK");
    return;
  }
  if (strcasecmp(tok, "SET") == 0) {
    while ((tok = strtok(NULL, " ")) != NULL) {
      char *eq = strchr(tok, '=');
      if (!eq) { Serial.println("ERR bad SET"); return; }
      *eq = 0;
      int v = constrain(atoi(eq + 1), 0, 255);
      int i = channelIndex(tok);
      if (i >= 0) setLevel(i, v);       // channels it hasn't got: ignored
    }
    Serial.println("OK");
    return;
  }
  if (strcasecmp(tok, "SHUTTER") == 0) {
    if (SHUTTER_PIN < 0) { Serial.println("ERR no shutter"); return; }
    tok = strtok(NULL, " ");
    long ms = tok ? constrain(atol(tok), 1L, 10000L) : 0;
    if (ms <= 0) { Serial.println("ERR bad SHUTTER"); return; }
    digitalWrite(SHUTTER_PIN, HIGH);
    delay(ms);
    digitalWrite(SHUTTER_PIN, LOW);
    Serial.println("OK");
    return;
  }
  if (strcasecmp(tok, "FOCUS") == 0) {
    if (FOCUS_PIN < 0) { Serial.println("ERR no focus"); return; }
    tok = strtok(NULL, " ");
    digitalWrite(FOCUS_PIN, (tok && tok[0] == '1') ? HIGH : LOW);
    Serial.println("OK");
    return;
  }
  Serial.print("ERR unknown ");
  Serial.println(tok);
}

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < 5; i++) {
    if (PINS[i] < 0) continue;
    pinMode(PINS[i], OUTPUT);
#if defined(ARDUINO_ARCH_RP2040)
    analogWriteFreq(20000);                  // Earle Philhower's RP2040 core
#elif defined(ESP32) || defined(TEENSYDUINO)
    analogWriteFrequency(PINS[i], 20000);    // ESP32 core 3.x, Teensy
#endif
    analogWrite(PINS[i], 0);
  }
  if (SHUTTER_PIN >= 0) { pinMode(SHUTTER_PIN, OUTPUT); digitalWrite(SHUTTER_PIN, LOW); }
  if (FOCUS_PIN >= 0) { pinMode(FOCUS_PIN, OUTPUT); digitalWrite(FOCUS_PIN, LOW); }
}

void loop() {
  while (Serial.available() > 0) {
    int b = Serial.read();
    if (b == '\n') {
      line[lineLen] = 0;
      if (lineLen > 0) handle(line);
      lineLen = 0;
    } else if (b >= 0x20 && b <= 0x7E && lineLen < MAX_LINE) {
      line[lineLen++] = (char)b;     // other bytes (another protocol's) dropped
    }
  }
  if (STATUS_EVERY_MS && millis() - lastStatus >= STATUS_EVERY_MS) {
    lastStatus = millis();
    sendStatus();
  }
}

// MIT License
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to
// deal in the Software without restriction, including without limitation the
// rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
// sell copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
// FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
// IN THE SOFTWARE.
