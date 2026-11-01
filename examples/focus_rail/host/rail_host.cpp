// rail_host.cpp: RailCore.h on a computer, with a simulated motor and end
// switch, so the reference firmware's protocol and motion logic can be tested
// without an ESP32 (FreeCCR's tests/test_focus_rail_firmware.py builds it).
//
//   g++ -std=c++11 -O1 -I../arduino/FocusRail rail_host.cpp -o rail_host
//   ./rail_host [SWITCH|STALL|NONE] [--realtime]
//
// Protocol lines go in on stdin, replies come out on stdout. Lines starting
// with '#' are for the harness only:
//   #tick <ms>      advance simulated time (not with --realtime)
//   #switch <pos>   the end switch closes at or below this position
//   #stall <pos>    the motor stalls (flags it) at or below this position
// With --realtime simulated time follows the clock.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/time.h>
#include <unistd.h>

#include <string>

#include "RailCore.h"

class SimMotor : public orp::Motor {
 public:
  double pos = 0;          // steps (true position, with sub-steps)
  long tgt = 0;
  bool run = false;
  int dir = 0;             // runToward: -1 / +1
  bool toward = false;
  uint32_t sps = 4000;
  uint32_t towardSps = 4000;
  bool enabled = true;
  double offset = 0;        // physical = pos + offset (forceStop renames positions)
  long switchAt = -1000000000L;   // physical positions (power-on = 0)
  long stallAt = -1000000000L;

  double physical() const { return pos + offset; }

  void moveTo(int32_t p) override { tgt = p; toward = false; run = (long)p != (long)pos; }
  void stop() override { run = false; toward = false; tgt = (long)pos; }
  void forceStop(int32_t p) override {
    run = false;
    toward = false;
    offset += pos - p;
    pos = p;
    tgt = p;
  }
  void runToward(int d, uint32_t s) override { dir = d; towardSps = s; toward = true; run = true; }
  int32_t position() override { return (int32_t)(pos < 0 ? pos - 0.5 : pos + 0.5); }
  int32_t target() override { return (int32_t)tgt; }
  bool running() override { return run; }
  void setSpeed(uint32_t s) override { sps = s; }
  void setAccel(uint32_t) override {}
  void enable(bool on) override { enabled = on; }
  bool homeSensed() override { return physical() <= switchAt || physical() <= stallAt; }

  void step(double ms) {
    if (!run) return;
    if (toward) {
      pos += (double)dir * towardSps * ms / 1000.0;
      if (physical() <= stallAt) pos = stallAt - offset;   // a hard end: it can't go further
      return;
    }
    double d = tgt - pos;
    double s = sps * ms / 1000.0;
    if (d > s) pos += s;
    else if (d < -s) pos -= s;
    else { pos = tgt; run = false; }
  }
};

static void writeLine(const char* line, void*) {
  fputs(line, stdout);
  fputc('\n', stdout);
  fflush(stdout);
}

static double nowMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return tv.tv_sec * 1000.0 + tv.tv_usec / 1000.0;
}

int main(int argc, char** argv) {
  int mode = orp::HOME_SWITCH;
  bool realtime = false;
  for (int i = 1; i < argc; ++i) {
    if (!strcmp(argv[i], "NONE")) mode = orp::HOME_NONE;
    else if (!strcmp(argv[i], "STALL")) mode = orp::HOME_STALL;
    else if (!strcmp(argv[i], "SWITCH")) mode = orp::HOME_SWITCH;
    else if (!strcmp(argv[i], "--realtime")) realtime = true;
  }
  orp::Config cfg;
  cfg.name = "Host Rail (simulated)";
  cfg.stepsPerMm = 400;
  cfg.stepsPerRev = 3200;
  cfg.hasLimits = true;
  cfg.minPos = 0;
  cfg.maxPos = 40000;
  cfg.homeMode = mode;
  cfg.homeFastSps = 8000;
  cfg.homeSlowSps = 400;
  cfg.homeBackoff = 200;
  cfg.homeMaxTravel = 50000;
  cfg.homeIgnoreSteps = 100;
  cfg.maxSps = 20000;
  cfg.maxAccel = 100000;
  cfg.defaultSps = 4000;
  cfg.defaultAccel = 20000;
  cfg.statusEveryMs = 200;

  SimMotor motor;
  orp::RailCore core(motor, cfg, writeLine, nullptr);
  double simMs = 0;
  double last = nowMs();
  std::string line;
  setvbuf(stdout, nullptr, _IOLBF, 0);

  auto advance = [&](double ms) {
    while (ms > 0) {
      double d = ms > 1 ? 1 : ms;
      motor.step(d);
      simMs += d;
      ms -= d;
      core.loop((uint32_t)simMs);
    }
  };

  for (;;) {
    if (realtime) {
      fd_set fds;
      FD_ZERO(&fds);
      FD_SET(0, &fds);
      struct timeval tv = {0, 1000};
      int r = select(1, &fds, nullptr, nullptr, &tv);
      double t = nowMs();
      advance(t - last);
      last = t;
      if (r <= 0) continue;
    }
    unsigned char ch;
    ssize_t got = read(0, &ch, 1);
    if (got <= 0) break;
    if (ch != '\n') { line.push_back((char)ch); continue; }
    if (!line.empty() && line[0] == '#') {
      long v = 0;
      if (!strncmp(line.c_str(), "#tick ", 6)) {
        if (!realtime) advance(atof(line.c_str() + 6));
      } else if (!strncmp(line.c_str(), "#switch ", 8)) {
        motor.switchAt = atol(line.c_str() + 8);
      } else if (!strncmp(line.c_str(), "#stall ", 7)) {
        motor.stallAt = atol(line.c_str() + 7);
      }
      (void)v;
      writeLine("#ok", nullptr);
    } else {
      line.push_back('\n');
      core.feed((const uint8_t*)line.data(), line.size(), (uint32_t)simMs);
      core.loop((uint32_t)simMs);
    }
    line.clear();
  }
  return 0;
}
