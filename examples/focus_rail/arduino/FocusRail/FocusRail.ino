// FocusRail: Open Rail Protocol v1 reference firmware for an ESP32 driving a
// NEMA 17 through a TMC2209, for a camera on a lead-screw focusing rail.
// FreeCCR's Live Capture Monitor (Focus Rail section) finds it on its USB
// serial port; the protocol is in FreeCCR's docs/open-rail-protocol.md.
// MIT licensed (see RailCore.h).
//
// Libraries (Arduino Library Manager): FastAccelStepper (hardware-timed step
// pulses with acceleration) and TMCStepper (the TMC2209's UART settings).
// Board: any ESP32 (DevKit with a CP2102/CH340, or an S3 on its native USB).
//
// Wiring, in short (check your driver board's pinout):
//   ESP32 STEP_PIN -> TMC2209 STEP      ESP32 DIR_PIN -> DIR
//   ESP32 EN_PIN   -> EN (low = on)     ESP32 GND     -> GND (logic)
//   ESP32 TMC_TX_PIN -> 1 kOhm -> PDN_UART <- ESP32 TMC_RX_PIN (straight)
//   MS1, MS2 to GND (UART address 0)    VIO -> 3.3 V
//   VM -> 12-24 V motor supply, with a 100 uF capacitor across it at the driver
//   Homing on a switch: a normally-open switch from SWITCH_PIN to GND at the
//   end of travel. Homing on stall: DIAG -> DIAG_PIN, no switch needed.
// Never plug or unplug the motor with VM powered: it kills the driver.
#include <FastAccelStepper.h>
#include <TMCStepper.h>

#include "RailCore.h"

// ---- your rail ----------------------------------------------------------
#define RAIL_NAME "DIY Focus Rail (ESP32 + TMC2209)"
#define FULL_STEPS_PER_REV 200      // a 1.8 degree NEMA 17
#define MICROSTEPS 16               // interpolated to 256 inside the TMC2209
#define LEAD_MM 8.0f                // mm per screw turn: T8x8 = 8, T8x2 = 2
#define TRAVEL_MM 100.0f            // soft limit from home (applies once homed)
#define RUN_CURRENT_MA 600          // RMS; a small NEMA 17 is happy at 400-800
#define HOLD_FRACTION 0.5f          // current while standing still
#define INVERT_DIR false            // true if HOME runs away from the end
#define SPEED_MM_S 8.0f             // normal moves
#define ACCEL_MM_S2 50.0f

// How HOME finds zero: orp::HOME_SWITCH, orp::HOME_STALL or orp::HOME_NONE
// (none: no HOME; use Set Zero in FreeCCR). Undecided? Start with HOME_NONE.
#define HOME_MODE orp::HOME_NONE
#define HOME_FAST_MM_S 4.0f
#define HOME_SLOW_MM_S 0.5f
#define HOME_BACKOFF_MM 0.5f
#define STALL_THRESHOLD 60          // SGTHRS for stall homing: higher = stops sooner

// ---- pins (an ESP32 DevKit; keep off the strapping pins 0, 2, 5, 12, 15) ---
#define STEP_PIN 25
#define DIR_PIN 26
#define EN_PIN 27
#define TMC_RX_PIN 16
#define TMC_TX_PIN 17
#define DIAG_PIN 34                 // input only, fine for DIAG
#define SWITCH_PIN 33               // has a pull-up
#define R_SENSE 0.11f               // most TMC2209 boards
#define DRIVER_ADDRESS 0b00

static const float STEPS_PER_MM = FULL_STEPS_PER_REV * MICROSTEPS / LEAD_MM;

FastAccelStepperEngine engine;
FastAccelStepper* stepper = nullptr;
TMC2209Stepper driver(&Serial2, R_SENSE, DRIVER_ADDRESS);
bool driverAnswers = false;

class HwMotor : public orp::Motor {
 public:
  uint32_t speed = 1000;
  void moveTo(int32_t p) override {
    stepper->setSpeedInHz(speed);
    stepper->moveTo(p);
  }
  void stop() override { stepper->stopMove(); }
  void forceStop(int32_t p) override { stepper->forceStopAndNewPosition(p); }
  void runToward(int dir, uint32_t sps) override {
    stepper->setSpeedInHz(sps);
    if (dir > 0) stepper->runForward();
    else stepper->runBackward();
  }
  int32_t position() override { return stepper->getCurrentPosition(); }
  int32_t target() override { return stepper->targetPos(); }
  bool running() override { return stepper->isRunning(); }
  void setSpeed(uint32_t sps) override { speed = sps; stepper->setSpeedInHz(sps); }
  void setAccel(uint32_t a) override { stepper->setAcceleration(a); }
  void enable(bool on) override {
    if (on) stepper->enableOutputs();
    else stepper->disableOutputs();
  }
  bool homeSensed() override {
    if (HOME_MODE == orp::HOME_SWITCH) return digitalRead(SWITCH_PIN) == LOW;
    if (HOME_MODE == orp::HOME_STALL) return digitalRead(DIAG_PIN) == HIGH;
    return false;
  }
  bool driverOk() override { return driverAnswers; }
};

HwMotor motor;
orp::RailCore* core = nullptr;

static void writeLine(const char* line, void*) {
  Serial.print(line);
  Serial.print('\n');
}

static orp::Config makeConfig() {
  orp::Config c;
  c.name = RAIL_NAME;
  c.stepsPerMm = STEPS_PER_MM;
  c.hasLimits = HOME_MODE != orp::HOME_NONE;
  c.minPos = 0;
  c.maxPos = (int32_t)(TRAVEL_MM * STEPS_PER_MM);
  c.homeMode = HOME_MODE;
  c.homeFastSps = (uint32_t)(HOME_FAST_MM_S * STEPS_PER_MM);
  c.homeSlowSps = (uint32_t)(HOME_SLOW_MM_S * STEPS_PER_MM);
  c.homeBackoff = (int32_t)(HOME_BACKOFF_MM * STEPS_PER_MM);
  c.homeMaxTravel = (int32_t)((TRAVEL_MM + 10.0f) * STEPS_PER_MM);
  c.homeIgnoreSteps = (int32_t)(0.5f * STEPS_PER_MM);   // the stall flag while it starts
  c.maxSps = (uint32_t)(20.0f * STEPS_PER_MM);
  c.maxAccel = (uint32_t)(500.0f * STEPS_PER_MM);
  c.defaultSps = (uint32_t)(SPEED_MM_S * STEPS_PER_MM);
  c.defaultAccel = (uint32_t)(ACCEL_MM_S2 * STEPS_PER_MM);
  c.statusEveryMs = 200;
  return c;
}

static void setupDriver() {
  Serial2.begin(115200, SERIAL_8N1, TMC_RX_PIN, TMC_TX_PIN);
  driver.begin();
  driver.pdn_disable(true);       // PDN_UART is the UART, not power-down
  driver.I_scale_analog(false);   // current from the register, not VREF
  driver.mstep_reg_select(true);  // microsteps from the register, not MS1/MS2
  driver.toff(4);
  driver.blank_time(24);
  driver.rms_current(RUN_CURRENT_MA, HOLD_FRACTION);
  driver.microsteps(MICROSTEPS);
  driver.intpol(true);
  driver.en_spreadCycle(false);   // StealthChop: quiet, and stall detection needs it
  driver.pwm_autoscale(true);
  driver.TPWMTHRS(0);             // StealthChop at every speed
  driver.TCOOLTHRS(0xFFFFF);      // stall flag (DIAG) at every speed
  driver.SGTHRS(STALL_THRESHOLD);
  driverAnswers = driver.test_connection() == 0;
}

void setup() {
  Serial.begin(115200);
  pinMode(SWITCH_PIN, INPUT_PULLUP);
  pinMode(DIAG_PIN, INPUT);
  setupDriver();
  engine.init();
  stepper = engine.stepperConnectToPin(STEP_PIN);
  stepper->setDirectionPin(DIR_PIN, !INVERT_DIR);
  stepper->setEnablePin(EN_PIN);            // low = enabled
  stepper->setAutoEnable(false);
  stepper->enableOutputs();                 // hold the camera from the start
  stepper->setCurrentPosition(0);           // not homed: position is relative
  static orp::Config cfg = makeConfig();
  static orp::RailCore railCore(motor, cfg, writeLine, nullptr);
  core = &railCore;
}

void loop() {
  uint32_t now = millis();
  while (Serial.available() > 0) {
    uint8_t b = (uint8_t)Serial.read();
    core->feed(&b, 1, now);
  }
  core->loop(now);
}
