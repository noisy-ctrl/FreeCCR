// RailCore.h: the Open Rail Protocol (ORP) v1 and a focus rail's motion
// logic, in plain C++ with no Arduino headers, so a computer can compile and
// test it (examples/focus_rail/host/rail_host.cpp; FreeCCR's tests run it).
// FocusRail.ino gives it an ESP32 + TMC2209 motor. The protocol is in
// FreeCCR's docs/open-rail-protocol.md. MIT licensed (see the end).
//
// Directions: position 0 is the home end and positions grow AWAY from it
// (invert the DIR pin in FocusRail.ino if HOME runs the wrong way).
#pragma once
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace orp {

// What the core needs from the motor (FocusRail.ino: FastAccelStepper).
class Motor {
 public:
  virtual ~Motor() {}
  virtual void moveTo(int32_t pos) = 0;               // retargets while moving
  virtual void stop() = 0;                             // decelerate to a stop
  virtual void forceStop(int32_t newPos) = 0;          // stop now; this is newPos
  virtual void runToward(int dir, uint32_t sps) = 0;   // keep going (homing)
  virtual int32_t position() = 0;
  virtual int32_t target() = 0;
  virtual bool running() = 0;
  virtual void setSpeed(uint32_t sps) = 0;
  virtual void setAccel(uint32_t spss) = 0;
  virtual void enable(bool on) = 0;
  virtual bool homeSensed() = 0;   // the switch is closed / the driver flags a stall
  virtual bool driverOk() { return true; }
};

enum HomeMode { HOME_NONE = 0, HOME_SWITCH = 1, HOME_STALL = 2 };

// Steps per turn of the SCREW when the motor drives it through a belt: the
// motor turns screwTeeth / motorTeeth times per screw turn (a big pulley on
// the screw = finer steps and more torque there; 1 and 1 for a coupler).
inline float screwStepsPerTurn(float fullSteps, float microsteps, float motorTeeth,
                               float screwTeeth) {
  if (motorTeeth <= 0 || screwTeeth <= 0) return fullSteps * microsteps;
  return fullSteps * microsteps * screwTeeth / motorTeeth;
}

struct Config {
  const char* name;
  float stepsPerMm;          // 0: unknown
  float stepsPerRev;         // steps per screw turn (0: unknown; a belt makes it
                             // fractional, 2133.33); hosts show turns
  bool hasLimits;            // soft limits (only once homed)
  int32_t minPos, maxPos;
  int homeMode;              // HomeMode
  uint32_t homeFastSps;      // toward the end
  uint32_t homeSlowSps;      // the second, slow touch (switch only)
  int32_t homeBackoff;       // steps off the end after homing (the final position)
  int32_t homeMaxTravel;     // give up after this many steps
  int32_t homeIgnoreSteps;   // stall: ignore the flag while it gets going
  uint32_t maxSps, maxAccel;
  uint32_t defaultSps, defaultAccel;
  uint32_t statusEveryMs;    // STATUS lines while moving
};

typedef void (*WriteLine)(const char* line, void* ctx);

class RailCore {
 public:
  static const size_t MAX_LINE = 128;

  RailCore(Motor& motor, const Config& cfg, WriteLine out, void* ctx)
      : m_(motor), c_(cfg), out_(out), ctx_(ctx) {
    m_.setSpeed(c_.defaultSps);
    m_.setAccel(c_.defaultAccel);
  }

  bool homed() const { return homed_; }
  bool busy() const { return moveActive_ || home_ != H_IDLE; }

  // Bytes from the host. Anything outside printable ASCII is dropped (another
  // protocol's bytes on the same port are harmless); CR is ignored.
  void feed(const uint8_t* data, size_t n, uint32_t nowMs) {
    for (size_t i = 0; i < n; ++i) {
      uint8_t b = data[i];
      if (b == '\n') {
        buf_[len_] = 0;
        if (len_) handle(buf_, nowMs);
        len_ = 0;
      } else if (b >= 0x20 && b <= 0x7E && len_ < MAX_LINE - 1) {
        buf_[len_++] = (char)b;
      }
    }
  }

  // Call often: finishes moves and homing, sends DONE and STATUS lines.
  void loop(uint32_t nowMs) {
    if (home_ != H_IDLE) homeStep(nowMs);
    if (moveActive_ && home_ == H_IDLE && !m_.running()) {
      moveActive_ = false;
      char line[96];
      int k = snprintf(line, sizeof line, "DONE%s POS=%ld", idText(), (long)m_.position());
      if (stopped_) k += snprintf(line + k, sizeof line - k, " STOPPED=1");
      if (limited_) k += snprintf(line + k, sizeof line - k, " LIMIT=1");
      if (fail_[0]) snprintf(line + k, sizeof line - k, " FAIL=%s", fail_);
      out_(line, ctx_);
      sendStatus();
    }
    if (busy() && c_.statusEveryMs && nowMs - lastStatus_ >= c_.statusEveryMs) {
      lastStatus_ = nowMs;
      sendStatus();
    }
  }

 private:
  enum Home { H_IDLE, H_FAST, H_BACKOFF, H_SLOW, H_FINAL, H_ABORT };

  Motor& m_;
  Config c_;
  WriteLine out_;
  void* ctx_;
  char buf_[MAX_LINE];
  size_t len_ = 0;
  bool homed_ = false;
  bool enabled_ = true;
  bool moveActive_ = false;
  bool stopped_ = false;
  bool limited_ = false;
  bool hasId_ = false;
  long id_ = 0;
  char idBuf_[24];
  char fail_[48] = {0};
  Home home_ = H_IDLE;
  int32_t homeFrom_ = 0;
  uint32_t lastStatus_ = 0;

  void reply(const char* s) { out_(s, ctx_); }

  const char* idText() {
    if (!hasId_) return "";
    snprintf(idBuf_, sizeof idBuf_, " ID=%ld", id_);
    return idBuf_;
  }

  void sendStatus() {
    char line[96];
    snprintf(line, sizeof line, "STATUS POS=%ld MOVING=%d HOMED=%d ENABLED=%d DRIVER=%d",
             (long)m_.position(), busy() ? 1 : 0, homed_ ? 1 : 0, enabled_ ? 1 : 0,
             m_.driverOk() ? 1 : 0);
    reply(line);
  }

  static bool parseLong(const char* s, long* v) {
    if (!s || !*s) return false;
    char* end = nullptr;
    long x = strtol(s, &end, 10);
    if (end == s || *end) return false;
    *v = x;
    return true;
  }

  // "ID=7" among the tokens after the command; false on a bad token.
  bool takeId(char** tok, int ntok, int from) {
    hasId_ = false;
    for (int i = from; i < ntok; ++i) {
      if (!strncmp(tok[i], "ID=", 3)) {
        long v;
        if (!parseLong(tok[i] + 3, &v)) return false;
        hasId_ = true;
        id_ = v;
      } else {
        return false;
      }
    }
    return true;
  }

  int32_t clampTarget(long t) {
    limited_ = false;
    if (homed_ && c_.hasLimits) {
      if (t < c_.minPos) { t = c_.minPos; limited_ = true; }
      if (t > c_.maxPos) { t = c_.maxPos; limited_ = true; }
    }
    return (int32_t)t;
  }

  void startMove(long target) {
    int32_t t = clampTarget(target);
    stopped_ = false;
    fail_[0] = 0;
    moveActive_ = true;
    m_.moveTo(t);
  }

  void handle(char* line, uint32_t now) {
    char* tok[8];
    int n = 0;
    for (char* p = line; *p; ++p) *p = (char)toupper((unsigned char)*p);
    for (char* p = strtok(line, " \t"); p && n < 8; p = strtok(nullptr, " \t")) tok[n++] = p;
    if (!n) return;
    const char* cmd = tok[0];
    if (!strcmp(cmd, "HELLO")) {
      char l[MAX_LINE + 64];
      int k = snprintf(l, sizeof l, "ORP 1 STEPS_PER_MM=%g", (double)c_.stepsPerMm);
      if (c_.stepsPerRev > 0)
        k += snprintf(l + k, sizeof l - k, " STEPS_PER_REV=%g", (double)c_.stepsPerRev);
      if (c_.hasLimits) k += snprintf(l + k, sizeof l - k, " MIN=%ld MAX=%ld",
                                      (long)c_.minPos, (long)c_.maxPos);
      const char* hm = c_.homeMode == HOME_SWITCH ? "SWITCH"
                       : c_.homeMode == HOME_STALL ? "STALL" : "NONE";
      snprintf(l + k, sizeof l - k, " HOME=%s NAME=%s", hm, c_.name);
      reply(l);
    } else if (!strcmp(cmd, "STATUS")) {
      sendStatus();
    } else if (!strcmp(cmd, "GOTO") || !strcmp(cmd, "MOVE")) {
      long v;
      if (home_ != H_IDLE) return reply("ERR homing");
      if (n < 2 || !parseLong(tok[1], &v) || !takeId(tok, n, 2)) return reply("ERR bad move");
      if (!enabled_) return reply("ERR motor off (ENABLE 1)");
      if (!strcmp(cmd, "MOVE")) {
        long from = (moveActive_ && m_.running()) ? m_.target() : m_.position();
        v += from;
      }
      startMove(v);
      lastStatus_ = now;
      reply("OK");
    } else if (!strcmp(cmd, "STOP")) {
      if (home_ != H_IDLE) {
        m_.stop();
        home_ = H_ABORT;
        stopped_ = true;
      } else if (moveActive_) {
        m_.stop();
        stopped_ = true;
      }
      reply("OK");
    } else if (!strcmp(cmd, "ZERO")) {
      if (busy() || m_.running()) return reply("ERR moving");
      m_.forceStop(0);
      homed_ = false;
      reply("OK");
    } else if (!strcmp(cmd, "HOME")) {
      if (c_.homeMode == HOME_NONE) return reply("ERR no home");
      if (home_ != H_IDLE) return reply("ERR homing");
      if (!takeId(tok, n, 1)) return reply("ERR bad HOME");
      if (!enabled_) return reply("ERR motor off (ENABLE 1)");
      if (moveActive_) m_.stop();
      homed_ = false;
      stopped_ = limited_ = false;
      fail_[0] = 0;
      moveActive_ = true;
      homeFrom_ = m_.position();
      home_ = H_FAST;
      m_.runToward(-1, c_.homeFastSps);
      lastStatus_ = now;
      reply("OK");
    } else if (!strcmp(cmd, "SPEED")) {
      long v, a = 0;
      if (n < 2 || !parseLong(tok[1], &v) || v <= 0) return reply("ERR bad SPEED");
      if (n > 2) {
        if (strncmp(tok[2], "ACCEL=", 6) || !parseLong(tok[2] + 6, &a) || a <= 0)
          return reply("ERR bad SPEED");
      }
      m_.setSpeed((uint32_t)(v > (long)c_.maxSps ? c_.maxSps : v));
      if (a) m_.setAccel((uint32_t)(a > (long)c_.maxAccel ? c_.maxAccel : a));
      reply("OK");
    } else if (!strcmp(cmd, "ENABLE")) {
      if (n < 2 || (strcmp(tok[1], "0") && strcmp(tok[1], "1"))) return reply("ERR bad ENABLE");
      if (busy()) return reply("ERR moving");
      enabled_ = tok[1][0] == '1';
      m_.enable(enabled_);
      if (!enabled_) homed_ = false;   // it can be turned by hand now
      reply("OK");
      sendStatus();
    } else {
      char l[64];
      snprintf(l, sizeof l, "ERR unknown %.40s", cmd);
      reply(l);
    }
  }

  void homeFail(const char* why) {
    m_.stop();
    snprintf(fail_, sizeof fail_, "%s", why);
    home_ = H_IDLE;      // the DONE goes out once it has stopped
  }

  long travelled() const {
    long d = (long)m_.position() - (long)homeFrom_;
    return d < 0 ? -d : d;
  }

  void homeStep(uint32_t now) {
    (void)now;
    switch (home_) {
      case H_FAST:
        if (m_.homeSensed() && (c_.homeMode != HOME_STALL || travelled() > c_.homeIgnoreSteps)) {
          m_.forceStop(0);
          homeFrom_ = 0;
          m_.moveTo(c_.homeBackoff);
          home_ = c_.homeMode == HOME_SWITCH ? H_BACKOFF : H_FINAL;
        } else if (travelled() > c_.homeMaxTravel) {
          homeFail(c_.homeMode == HOME_SWITCH ? "no switch found" : "no stall found");
        } else if (!m_.running()) {
          homeFail("the motor stopped");
        }
        break;
      case H_BACKOFF:
        if (!m_.running()) {
          if (m_.homeSensed()) { homeFail("the switch stays closed"); break; }
          homeFrom_ = m_.position();
          home_ = H_SLOW;
          m_.runToward(-1, c_.homeSlowSps);
        }
        break;
      case H_SLOW:
        if (m_.homeSensed()) {
          m_.forceStop(0);
          m_.moveTo(c_.homeBackoff);
          home_ = H_FINAL;
        } else if (travelled() > 2 * c_.homeBackoff + 50) {
          homeFail("the switch didn't close again");
        }
        break;
      case H_FINAL:
        if (!m_.running()) {
          homed_ = true;
          home_ = H_IDLE;           // loop() sends the DONE
        }
        break;
      case H_ABORT:
        if (!m_.running()) home_ = H_IDLE;
        break;
      default:
        break;
    }
  }
};

}  // namespace orp

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
