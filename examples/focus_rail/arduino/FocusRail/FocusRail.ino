// FocusRail: Open Rail Protocol v1 reference firmware for an ESP32 driving a
// NEMA 17 through a TMC2209, for a camera on a lead-screw focusing rail.
// FreeCCR's Live Capture Monitor (Focus Rail section) finds it on its USB
// serial port; the protocol is in FreeCCR's docs/open-rail-protocol.md.
// MIT licensed (see RailCore.h).
//
// Libraries (Arduino Library Manager): FastAccelStepper (hardware-timed step
// pulses with acceleration) and TMCStepper (the TMC2209's UART settings).
// Board: an ESP32-S3 (DevKitC-1, e.g. the N16R8) or a classic ESP32 DevKit;
// the pins below follow whichever you compile for. The step-by-step guide,
// with a wiring table and a first test, is examples/focus_rail/README.md.
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
//
// A ball screw turns back easily under load: on a vertical stand the
// camera's weight can turn it, and the camera slides down, whenever the motor
// has no current (motor supply off, the ESP32 restarting, ENABLE 0). Keep the
// motor supply on while the camera is on the rail, and a stop collar on the
// rail just below the focus range is cheap insurance for the lens and the
// film. Holding a camera of about 2 kg on a 4-5 mm lead takes roughly
// 15 mN m, a small share of a NEMA 17's torque even at the hold current;
// on a 10 mm lead it is about 2.5 times that, so raise HOLD_FRACTION.
#include <FastAccelStepper.h>
#include <TMCStepper.h>

#include "RailCore.h"

// ---- your rail ----------------------------------------------------------
#define RAIL_NAME "DIY Focus Rail (ESP32 + TMC2209)"
#define FULL_STEPS_PER_REV 200      // a 1.8 degree NEMA 17 (pancake ones too)
#define MICROSTEPS 16               // interpolated to 256 inside the TMC2209
#define LEAD_MM 4.0f                // mm per screw turn. A ball screw's code ends in
                                    // it: SFU1204 = 4, SFU1605 = 5, SFU1610 = 10
                                    // (a T8x8 lead screw = 8, T8x2 = 2)
// A belt between the motor and the screw: the teeth on each pulley (1 and 1
// for a coupler straight onto the screw). The motor turns SCREW / MOTOR times
// per screw turn, so a BIG pulley on the SCREW gives finer steps and more
// torque there: 20 on the motor and 30 on the screw = 1200 steps a mm, 0.83 um
// a step, 1.5 times the motor's torque at the screw. (The other way round, 30
// on the motor and 20 on the screw, is 533.3 a mm, 1.9 um a step and 2/3 of
// the torque.) These must match the pulleys fitted, or FreeCCR's millimetres
// and the autofocus distances will be wrong.
#define MOTOR_PULLEY_TEETH 20
#define SCREW_PULLEY_TEETH 30
#define SCREW_LENGTH_MM 300.0f      // the screw's whole length: HOME gives up after this
#define TRAVEL_MM 200.0f            // soft limit from home (applies once homed). A 300 mm
                                    // SFU1204 leaves roughly 200 mm of stroke once its end
                                    // blocks and the nut are on: measure yours and set it
                                    // a little short of the far end
#define RUN_CURRENT_MA 600          // RMS. Never above the current on the motor's label;
                                    // a pancake NEMA 17 is usually rated about 1 A
#define HOLD_FRACTION 0.5f          // current while standing still: it holds the camera
                                    // up through the belt. Raise it (up to 1.0) if the
                                    // camera creeps down when the rail stops
#define INVERT_DIR false            // true if a + move takes the camera the wrong way
#define SPEED_MM_S 2.0f             // normal moves: half a turn of a 4 mm screw a second
#define ACCEL_MM_S2 10.0f
#define MAX_SPEED_MM_S 10.0f        // the fastest FreeCCR's SPEED may ask for

// How HOME finds zero: orp::HOME_SWITCH, orp::HOME_STALL or orp::HOME_NONE
// (none: no HOME; use Set Zero in FreeCCR). Undecided? Start with HOME_NONE.
#define HOME_MODE orp::HOME_NONE
#define HOME_FAST_MM_S 2.0f
#define HOME_SLOW_MM_S 0.25f
#define HOME_BACKOFF_MM 0.5f
#define STALL_THRESHOLD 60          // SGTHRS for stall homing: higher = stops sooner

// ---- pins ---------------------------------------------------------------
#if defined(CONFIG_IDF_TARGET_ESP32S3)
// ESP32-S3 DevKitC-1 (N16R8 and the rest): all on the headers, and clear of
// what the module uses: 26-32 (flash), 35-37 (octal PSRAM on R8 modules), the
// strapping pins 0, 3, 45, 46, USB 19/20, UART0 43/44 and the RGB LED (38/48).
#define STEP_PIN 4
#define DIR_PIN 5
#define EN_PIN 6
#define TMC_TX_PIN 17               // UART1's usual pins on an S3
#define TMC_RX_PIN 18
#define DIAG_PIN 7
#define SWITCH_PIN 15               // has a pull-up
#else
// A classic ESP32 DevKit (keep off the strapping pins 0, 2, 5, 12, 15).
#define STEP_PIN 25
#define DIR_PIN 26
#define EN_PIN 27
#define TMC_RX_PIN 16
#define TMC_TX_PIN 17
#define DIAG_PIN 34                 // input only, fine for DIAG
#define SWITCH_PIN 33               // has a pull-up
#endif
#define R_SENSE 0.11f               // most TMC2209 boards
#define DRIVER_ADDRESS 0b00

// steps per turn of the screw, through the belt, and per millimetre
static const float STEPS_PER_REV = orp::screwStepsPerTurn(
    FULL_STEPS_PER_REV, MICROSTEPS, MOTOR_PULLEY_TEETH, SCREW_PULLEY_TEETH);
static const float STEPS_PER_MM = STEPS_PER_REV / LEAD_MM;

FastAccelStepperEngine engine;
FastAccelStepper* stepper = nullptr;
#define TMCSerial Serial1            // UART1: every ESP32 has it (Serial2 not on all)
TMC2209Stepper driver(&TMCSerial, R_SENSE, DRIVER_ADDRESS);
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
  c.stepsPerRev = STEPS_PER_REV;
  c.hasLimits = HOME_MODE != orp::HOME_NONE;
  c.minPos = 0;
  c.maxPos = (int32_t)(TRAVEL_MM * STEPS_PER_MM);
  c.homeMode = HOME_MODE;
  c.homeFastSps = (uint32_t)(HOME_FAST_MM_S * STEPS_PER_MM);
  c.homeSlowSps = (uint32_t)(HOME_SLOW_MM_S * STEPS_PER_MM);
  c.homeBackoff = (int32_t)(HOME_BACKOFF_MM * STEPS_PER_MM);
  c.homeMaxTravel = (int32_t)(SCREW_LENGTH_MM * STEPS_PER_MM);   // from anywhere on it
  c.homeIgnoreSteps = (int32_t)(0.5f * STEPS_PER_MM);   // the stall flag while it starts
  c.maxSps = (uint32_t)(MAX_SPEED_MM_S * STEPS_PER_MM);
  c.maxAccel = (uint32_t)(100.0f * STEPS_PER_MM);
  c.defaultSps = (uint32_t)(SPEED_MM_S * STEPS_PER_MM);
  c.defaultAccel = (uint32_t)(ACCEL_MM_S2 * STEPS_PER_MM);
  c.statusEveryMs = 200;
  return c;
}

// The TMC2209 only answers on its UART while its motor supply (VM) is on, and
// forgets these settings when VM goes off (it then microsteps by its MS1/MS2
// pins: 1/8 with both to GND, which would halve every distance). So they are
// written whenever it starts answering, or reports that it was reset, and
// checked once a second (superviseDriver).
static void writeDriverSettings() {
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
  driver.GSTAT(0b111);            // clear "reset" so the next one shows
}

static void setupDriver() {
  TMCSerial.begin(115200, SERIAL_8N1, TMC_RX_PIN, TMC_TX_PIN);
  driver.begin();
  driverAnswers = driver.test_connection() == 0;
  if (driverAnswers) writeDriverSettings();
}

static void superviseDriver(uint32_t now) {
  static uint32_t last = 0;
  if (now - last < 1000) return;
  last = now;
  bool ok = driver.test_connection() == 0;
  if (ok && (!driverAnswers || (driver.GSTAT() & 0b001))) writeDriverSettings();
  driverAnswers = ok;             // STATUS says DRIVER=0 while it doesn't answer
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
  superviseDriver(now);
}
