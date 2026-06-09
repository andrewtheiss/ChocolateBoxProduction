#include <ArduinoJson.h>
#include <EEPROM.h>

// ── Motor registry ──────────────────────────
#define MAX_MOTORS 6

// EEPROM-persisted station id so a board remembers its name across reboots.
#define EEPROM_MAGIC 0x43
#define EEPROM_ADDR_MAGIC 0
#define EEPROM_ADDR_LEN 1
#define EEPROM_ADDR_NAME 2
#define MAX_ID_LEN 15

// EEPROM-persisted device config (motors/limits/encoders), compact binary.
// Layout: [magic][version][motorCount][limitCount][encoderCount] then records.
//   motor:   nameLen,name, pul, dir, ena, flags(bit0=reversed)
//   limit:   nameLen,name, pin, flags(bit0=normallyOpen), stopMask(bit i = motor i)
//   encoder: nameLen,name, motorLen,motor, pinA, pinB(0xFF=-1), cpr(4 bytes LE)
#define EEPROM_CFG_ADDR 20
#define EEPROM_CFG_MAGIC 0x4D
#define EEPROM_CFG_VERSION 1

const char* FIRMWARE_NAME = "generic";
const char* FIRMWARE_VERSION = "2.8.1";
const char* FIRMWARE_BUILD = __DATE__ " " __TIME__;
const int DEFAULT_SPEED_US = 62;
#define MAX_LIMITS 6
#define MAX_ENCODERS 4
#define MAX_STOPS 6

struct Motor {
  char name[16];
  int pulPin;
  int dirPin;
  int enaPin;
  bool reversed;   // swap direction logic
  bool running;
  bool configured;
  long position;   // signed step position from last zero (+ = logical forward)
};

Motor motors[MAX_MOTORS];
int motorCount = 0;
volatile bool globalStop = false;
String stationId = "generic";
// Each active motor keeps its own speed, step target, and pulse state so a
// single run can drive motors at independent us/step rates simultaneously.
Motor* activeMotors[MAX_MOTORS];
bool activeForward[MAX_MOTORS];     // logical direction per active motor (for position)
long activeStepTarget[MAX_MOTORS];  // steps to run for this motor
long activeStepCount[MAX_MOTORS];   // steps completed
int activeSpeedUs[MAX_MOTORS];      // us per half-pulse for this motor
unsigned long activeLastPulse[MAX_MOTORS];
bool activePulseHigh[MAX_MOTORS];
bool activeDone[MAX_MOTORS];
int activeMotorCount = 0;
bool asyncRunActive = false;

// ── Limit switch registry ───────────────────
struct LimitSwitch {
  char name[16];
  int pin;
  bool normallyOpen;     // true: switch shorts to GND when pressed (active LOW)
  bool configured;
  bool tripped;          // latched until clear_limit
  bool lastActive;
  char stopNames[MAX_STOPS][16];  // motors to stop on trip; empty = stop all
  int stopCount;
};

LimitSwitch limits[MAX_LIMITS];
int limitCount = 0;

// ── Encoder registry (reserved; counts surfaced via get_encoder) ──
struct Encoder {
  char name[16];
  char motor[16];
  int pinA;
  int pinB;
  long countsPerRev;
  long count;
  bool configured;
};

Encoder encoders[MAX_ENCODERS];
int encoderCount = 0;

// ── Helpers ─────────────────────────────────
Motor* findMotor(const char* name) {
  for (int i = 0; i < motorCount; i++) {
    if (motors[i].configured && strcmp(motors[i].name, name) == 0) {
      return &motors[i];
    }
  }
  return nullptr;
}

void sendOk(const char* msg = "ok") {
  StaticJsonDocument<128> resp;
  resp["status"] = msg;
  serializeJson(resp, Serial);
  Serial.println();
}

void sendError(const char* msg) {
  StaticJsonDocument<128> resp;
  resp["status"] = "error";
  resp["error"] = msg;
  serializeJson(resp, Serial);
  Serial.println();
}

void sendVersionInfo(const char* status = "ok") {
  StaticJsonDocument<192> resp;
  resp["status"] = status;
  resp["id"] = stationId;
  resp["firmware"] = FIRMWARE_NAME;
  resp["version"] = FIRMWARE_VERSION;
  resp["build"] = FIRMWARE_BUILD;
  serializeJson(resp, Serial);
  Serial.println();
}

bool anyMotorRunning() {
  for (int i = 0; i < motorCount; i++) {
    if (motors[i].configured && motors[i].running) {
      return true;
    }
  }
  return false;
}

void setMotorOutputState(Motor* m, bool forward) {
  bool dir = forward;
  if (m->reversed) dir = !dir;

  digitalWrite(m->dirPin, dir ? HIGH : LOW);
  digitalWrite(m->enaPin, LOW);
  m->running = true;
}

void stopMotorOutputState(Motor* m) {
  digitalWrite(m->pulPin, LOW);
  digitalWrite(m->enaPin, HIGH);
  m->running = false;
}

void clearAsyncRun() {
  for (int i = 0; i < activeMotorCount; i++) {
    if (activeMotors[i]) stopMotorOutputState(activeMotors[i]);
    activeMotors[i] = nullptr;
  }
  activeMotorCount = 0;
  asyncRunActive = false;
  globalStop = false;
}

void startAsyncRun(Motor* selected[], bool forwards[], int speeds[],
                   long targets[], int selectedCount) {
  activeMotorCount = selectedCount;
  globalStop = false;
  unsigned long now = micros();

  for (int i = 0; i < selectedCount; i++) {
    activeMotors[i] = selected[i];
    activeForward[i] = forwards[i];
    activeSpeedUs[i] = speeds[i] > 0 ? speeds[i] : DEFAULT_SPEED_US;
    activeStepTarget[i] = targets[i];
    activeStepCount[i] = 0;
    activePulseHigh[i] = false;
    activeLastPulse[i] = now;
    activeDone[i] = (targets[i] <= 0);
    if (!activeDone[i]) {
      setMotorOutputState(activeMotors[i], forwards[i]);
    }
  }

  asyncRunActive = true;
}

// Convenience for uniform-speed runs (all motors share speed + step count).
void startAsyncRunUniform(Motor* selected[], bool forwards[], int selectedCount,
                          long steps, int speed_us) {
  int speeds[MAX_MOTORS];
  long targets[MAX_MOTORS];
  for (int i = 0; i < selectedCount; i++) {
    speeds[i] = speed_us;
    targets[i] = steps;
  }
  startAsyncRun(selected, forwards, speeds, targets, selectedCount);
}

void serviceAsyncRun() {
  if (!asyncRunActive) return;

  if (globalStop) {
    clearAsyncRun();
    return;
  }

  unsigned long now = micros();
  bool anyActive = false;

  for (int i = 0; i < activeMotorCount; i++) {
    if (activeDone[i]) continue;
    if ((unsigned long)(now - activeLastPulse[i]) < (unsigned long)activeSpeedUs[i]) {
      anyActive = true;
      continue;
    }
    activeLastPulse[i] = now;
    activePulseHigh[i] = !activePulseHigh[i];
    digitalWrite(activeMotors[i]->pulPin, activePulseHigh[i] ? HIGH : LOW);

    if (!activePulseHigh[i]) {
      activeStepCount[i]++;
      activeMotors[i]->position += activeForward[i] ? 1 : -1;
      if (activeStepCount[i] >= activeStepTarget[i]) {
        activeDone[i] = true;
        stopMotorOutputState(activeMotors[i]);
      }
    }
    if (!activeDone[i]) anyActive = true;
  }

  if (!anyActive) clearAsyncRun();
}

void runSingleMotor(Motor* m, int steps, int speed_us, bool forward) {
  setMotorOutputState(m, forward);

  for (int i = 0; i < steps; i++) {
    if (globalStop) break;
    digitalWrite(m->pulPin, HIGH);
    delayMicroseconds(speed_us);
    digitalWrite(m->pulPin, LOW);
    delayMicroseconds(speed_us);
    m->position += forward ? 1 : -1;
  }

  stopMotorOutputState(m);
}

void runMotorGroup(Motor* selected[], bool forwards[], int selectedCount, int steps, int speed_us) {
  for (int i = 0; i < selectedCount; i++) {
    setMotorOutputState(selected[i], forwards[i]);
  }

  for (int step = 0; step < steps; step++) {
    if (globalStop) break;

    for (int i = 0; i < selectedCount; i++) {
      digitalWrite(selected[i]->pulPin, HIGH);
    }
    delayMicroseconds(speed_us);

    for (int i = 0; i < selectedCount; i++) {
      digitalWrite(selected[i]->pulPin, LOW);
    }
    delayMicroseconds(speed_us);

    for (int i = 0; i < selectedCount; i++) {
      selected[i]->position += forwards[i] ? 1 : -1;
    }
  }

  for (int i = 0; i < selectedCount; i++) {
    stopMotorOutputState(selected[i]);
  }
}

// ── Limit switches ──────────────────────────
LimitSwitch* findLimit(const char* name) {
  for (int i = 0; i < limitCount; i++) {
    if (limits[i].configured && strcmp(limits[i].name, name) == 0) {
      return &limits[i];
    }
  }
  return nullptr;
}

void stopAllMotorsNow() {
  globalStop = true;
  if (asyncRunActive) clearAsyncRun();
  for (int i = 0; i < motorCount; i++) {
    if (motors[i].configured) stopMotorOutputState(&motors[i]);
  }
}

void handleLimitTrip(LimitSwitch* lim) {
  // If no specific motors listed, stop everything; otherwise stop the named
  // motors. If any stopped motor is part of the active async run, abort it.
  if (lim->stopCount == 0) {
    stopAllMotorsNow();
  } else {
    bool hitActive = false;
    for (int i = 0; i < lim->stopCount; i++) {
      Motor* m = findMotor(lim->stopNames[i]);
      if (m) {
        for (int j = 0; j < activeMotorCount; j++) {
          if (activeMotors[j] == m) hitActive = true;
        }
        stopMotorOutputState(m);
      }
    }
    if (hitActive && asyncRunActive) clearAsyncRun();
  }

  StaticJsonDocument<128> evt;
  evt["event"] = "limit";
  evt["name"] = lim->name;
  evt["id"] = stationId;
  serializeJson(evt, Serial);
  Serial.println();
}

void serviceLimits() {
  for (int i = 0; i < limitCount; i++) {
    if (!limits[i].configured) continue;
    int reading = digitalRead(limits[i].pin);
    bool active = limits[i].normallyOpen ? (reading == LOW) : (reading == HIGH);
    if (active && !limits[i].lastActive && !limits[i].tripped) {
      limits[i].tripped = true;
      handleLimitTrip(&limits[i]);
    }
    limits[i].lastActive = active;
  }
}

Encoder* findEncoder(const char* name) {
  for (int i = 0; i < encoderCount; i++) {
    if (encoders[i].configured && strcmp(encoders[i].name, name) == 0) {
      return &encoders[i];
    }
  }
  return nullptr;
}

// ── Persistent station id (EEPROM) ──────────
void loadStationId() {
  if (EEPROM.read(EEPROM_ADDR_MAGIC) != EEPROM_MAGIC) {
    return;  // nothing valid stored; keep default "generic"
  }
  int len = EEPROM.read(EEPROM_ADDR_LEN);
  if (len <= 0 || len > MAX_ID_LEN) return;
  char buf[MAX_ID_LEN + 1];
  for (int i = 0; i < len; i++) {
    buf[i] = (char)EEPROM.read(EEPROM_ADDR_NAME + i);
  }
  buf[len] = '\0';
  stationId = String(buf);
}

void saveStationId(const char* id) {
  int len = strlen(id);
  if (len > MAX_ID_LEN) len = MAX_ID_LEN;
  EEPROM.update(EEPROM_ADDR_MAGIC, EEPROM_MAGIC);
  EEPROM.update(EEPROM_ADDR_LEN, len);
  for (int i = 0; i < len; i++) {
    EEPROM.update(EEPROM_ADDR_NAME + i, id[i]);
  }
}

// ── Persistent device config (EEPROM, binary) ──
int motorIndexByName(const char* nm) {
  for (int i = 0; i < motorCount; i++) {
    if (motors[i].configured && strcmp(motors[i].name, nm) == 0) return i;
  }
  return -1;
}

uint8_t limitStopMask(LimitSwitch* lim) {
  uint8_t mask = 0;
  for (int s = 0; s < lim->stopCount; s++) {
    int idx = motorIndexByName(lim->stopNames[s]);
    if (idx >= 0 && idx < 8) mask |= (uint8_t)(1 << idx);
  }
  return mask;
}

// Bytes the current config would occupy in EEPROM (for overflow guard).
int configByteSize() {
  int n = 5;  // header
  for (int i = 0; i < motorCount; i++) {
    if (!motors[i].configured) continue;
    int l = strlen(motors[i].name); if (l > 15) l = 15;
    n += 1 + l + 4;
  }
  for (int i = 0; i < limitCount; i++) {
    if (!limits[i].configured) continue;
    int l = strlen(limits[i].name); if (l > 15) l = 15;
    n += 1 + l + 3;
  }
  for (int i = 0; i < encoderCount; i++) {
    if (!encoders[i].configured) continue;
    int nl = strlen(encoders[i].name); if (nl > 15) nl = 15;
    int ml = strlen(encoders[i].motor); if (ml > 15) ml = 15;
    n += 1 + nl + 1 + ml + 2 + 4;
  }
  return n;
}

int cfgWriteStr(int addr, const char* s) {
  int len = strlen(s); if (len > 15) len = 15;
  EEPROM.update(addr++, (uint8_t)len);
  for (int i = 0; i < len; i++) EEPROM.update(addr++, (uint8_t)s[i]);
  return addr;
}

int cfgReadStr(int addr, char* out, int cap) {
  int len = EEPROM.read(addr++);
  for (int i = 0; i < len; i++) {
    uint8_t c = EEPROM.read(addr++);
    if (i < cap) out[i] = (char)c;
  }
  out[(len < cap) ? len : cap] = '\0';
  return addr;
}

bool saveConfig() {
  if (EEPROM_CFG_ADDR + configByteSize() > (int)EEPROM.length()) return false;
  int addr = EEPROM_CFG_ADDR;
  EEPROM.update(addr++, EEPROM_CFG_MAGIC);
  EEPROM.update(addr++, EEPROM_CFG_VERSION);
  EEPROM.update(addr++, (uint8_t)motorCount);
  EEPROM.update(addr++, (uint8_t)limitCount);
  EEPROM.update(addr++, (uint8_t)encoderCount);
  for (int i = 0; i < motorCount; i++) {
    if (!motors[i].configured) continue;
    addr = cfgWriteStr(addr, motors[i].name);
    EEPROM.update(addr++, (uint8_t)motors[i].pulPin);
    EEPROM.update(addr++, (uint8_t)motors[i].dirPin);
    EEPROM.update(addr++, (uint8_t)motors[i].enaPin);
    EEPROM.update(addr++, motors[i].reversed ? 0x01 : 0x00);
  }
  for (int i = 0; i < limitCount; i++) {
    if (!limits[i].configured) continue;
    addr = cfgWriteStr(addr, limits[i].name);
    EEPROM.update(addr++, (uint8_t)limits[i].pin);
    EEPROM.update(addr++, limits[i].normallyOpen ? 0x01 : 0x00);
    EEPROM.update(addr++, limitStopMask(&limits[i]));
  }
  for (int i = 0; i < encoderCount; i++) {
    if (!encoders[i].configured) continue;
    addr = cfgWriteStr(addr, encoders[i].name);
    addr = cfgWriteStr(addr, encoders[i].motor);
    EEPROM.update(addr++, (uint8_t)encoders[i].pinA);
    EEPROM.update(addr++, encoders[i].pinB < 0 ? 0xFF : (uint8_t)encoders[i].pinB);
    unsigned long cpr = (unsigned long)encoders[i].countsPerRev;
    EEPROM.update(addr++, (uint8_t)(cpr & 0xFF));
    EEPROM.update(addr++, (uint8_t)((cpr >> 8) & 0xFF));
    EEPROM.update(addr++, (uint8_t)((cpr >> 16) & 0xFF));
    EEPROM.update(addr++, (uint8_t)((cpr >> 24) & 0xFF));
  }
  return true;
}

void registerMotorEntry(const char* name, int pul, int dir, int ena, bool rev) {
  if (motorCount >= MAX_MOTORS || findMotor(name)) return;
  Motor* m = &motors[motorCount];
  strncpy(m->name, name, 15); m->name[15] = '\0';
  m->pulPin = pul; m->dirPin = dir; m->enaPin = ena; m->reversed = rev;
  m->running = false; m->configured = true; m->position = 0;
  motorCount++;
  pinMode(pul, OUTPUT); pinMode(dir, OUTPUT); pinMode(ena, OUTPUT);
  digitalWrite(ena, HIGH);
}

void registerLimitEntry(const char* name, int pin, bool no, uint8_t stopMask) {
  if (limitCount >= MAX_LIMITS) return;
  LimitSwitch* lim = &limits[limitCount];
  strncpy(lim->name, name, 15); lim->name[15] = '\0';
  lim->pin = pin; lim->normallyOpen = no; lim->configured = true;
  lim->tripped = false; lim->lastActive = false; lim->stopCount = 0;
  for (int i = 0; i < motorCount && i < 8; i++) {
    if ((stopMask & (1 << i)) && lim->stopCount < MAX_STOPS) {
      strncpy(lim->stopNames[lim->stopCount], motors[i].name, 15);
      lim->stopNames[lim->stopCount][15] = '\0';
      lim->stopCount++;
    }
  }
  pinMode(pin, INPUT_PULLUP);
  limitCount++;
}

void registerEncoderEntry(const char* name, const char* motor, int pinA, int pinB, long cpr) {
  if (encoderCount >= MAX_ENCODERS) return;
  Encoder* enc = &encoders[encoderCount];
  strncpy(enc->name, name, 15); enc->name[15] = '\0';
  strncpy(enc->motor, motor, 15); enc->motor[15] = '\0';
  enc->pinA = pinA; enc->pinB = pinB; enc->countsPerRev = cpr;
  enc->count = 0; enc->configured = true;
  pinMode(pinA, INPUT_PULLUP);
  if (pinB >= 0) pinMode(pinB, INPUT_PULLUP);
  encoderCount++;
}

void loadConfig() {
  if (EEPROM.read(EEPROM_CFG_ADDR) != EEPROM_CFG_MAGIC) return;
  int addr = EEPROM_CFG_ADDR + 1;
  uint8_t ver = EEPROM.read(addr++);
  if (ver != EEPROM_CFG_VERSION) return;
  uint8_t mc = EEPROM.read(addr++);
  uint8_t lc = EEPROM.read(addr++);
  uint8_t ec = EEPROM.read(addr++);
  char nm[17];
  char mtr[17];
  for (uint8_t i = 0; i < mc; i++) {
    addr = cfgReadStr(addr, nm, 16);
    int pul = EEPROM.read(addr++);
    int dir = EEPROM.read(addr++);
    int ena = EEPROM.read(addr++);
    uint8_t flags = EEPROM.read(addr++);
    registerMotorEntry(nm, pul, dir, ena, flags & 0x01);
  }
  for (uint8_t i = 0; i < lc; i++) {
    addr = cfgReadStr(addr, nm, 16);
    int pin = EEPROM.read(addr++);
    uint8_t flags = EEPROM.read(addr++);
    uint8_t stopMask = EEPROM.read(addr++);
    registerLimitEntry(nm, pin, flags & 0x01, stopMask);
  }
  for (uint8_t i = 0; i < ec; i++) {
    addr = cfgReadStr(addr, nm, 16);
    addr = cfgReadStr(addr, mtr, 16);
    int pinA = EEPROM.read(addr++);
    uint8_t pbRaw = EEPROM.read(addr++);
    int pinB = (pbRaw == 0xFF) ? -1 : (int)pbRaw;
    unsigned long cpr = (unsigned long)EEPROM.read(addr++);
    cpr |= ((unsigned long)EEPROM.read(addr++)) << 8;
    cpr |= ((unsigned long)EEPROM.read(addr++)) << 16;
    cpr |= ((unsigned long)EEPROM.read(addr++)) << 24;
    registerEncoderEntry(nm, mtr, pinA, pinB, (long)cpr);
  }
}

// ── Main ────────────────────────────────────
void setup() {
  Serial.begin(9600);
  while (!Serial) {}

  for (int i = 0; i < MAX_MOTORS; i++) {
    motors[i].configured = false;
  }
  for (int i = 0; i < MAX_LIMITS; i++) {
    limits[i].configured = false;
  }
  for (int i = 0; i < MAX_ENCODERS; i++) {
    encoders[i].configured = false;
  }

  loadStationId();
  loadConfig();
  sendVersionInfo("boot");
}

void loop() {
  serviceAsyncRun();
  serviceLimits();
  if (!Serial.available()) return;

  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, Serial);
  if (err) return;

  const char* cmd = doc["cmd"];
  if (!cmd) return;

  // ── identify ──
  if (strcmp(cmd, "identify") == 0) {
    StaticJsonDocument<128> resp;
    resp["id"] = stationId;
    resp["firmware"] = FIRMWARE_NAME;
    resp["version"] = FIRMWARE_VERSION;
    resp["build"] = FIRMWARE_BUILD;
    resp["motors"] = motorCount;
    serializeJson(resp, Serial);
    Serial.println();

  // ── version ──
  } else if (strcmp(cmd, "version") == 0) {
    sendVersionInfo();

  // ── set_id ──
  } else if (strcmp(cmd, "set_id") == 0) {
    const char* newId = doc["id"];
    if (newId) {
      stationId = String(newId);
      saveStationId(newId);
      sendOk("id_set");
    } else {
      sendError("missing id");
    }

  } else if (strcmp(cmd, "save_config") == 0) {
    if (!saveConfig()) {
      sendError("config too large for eeprom");
      return;
    }
    StaticJsonDocument<128> resp;
    resp["status"] = "config_saved";
    resp["motors"] = motorCount;
    resp["limits"] = limitCount;
    resp["encoders"] = encoderCount;
    resp["bytes"] = configByteSize();
    serializeJson(resp, Serial);
    Serial.println();

  } else if (strcmp(cmd, "clear_config") == 0) {
    EEPROM.update(EEPROM_CFG_ADDR, 0x00);  // invalidate magic
    sendOk("config_cleared");

  // ── add_motor ──
  } else if (strcmp(cmd, "add_motor") == 0) {
    if (motorCount >= MAX_MOTORS) {
      sendError("max motors reached");
      return;
    }
    const char* name = doc["name"];
    int pul = doc["pul_pin"] | -1;
    int dir = doc["dir_pin"] | -1;
    int ena = doc["ena_pin"] | -1;
    bool rev = doc["reversed"] | false;

    if (!name || pul < 0 || dir < 0 || ena < 0) {
      sendError("need name, pul_pin, dir_pin, ena_pin");
      return;
    }

    if (findMotor(name)) {
      sendError("motor name already exists");
      return;
    }

    Motor* m = &motors[motorCount];
    strncpy(m->name, name, 15);
    m->name[15] = '\0';
    m->pulPin = pul;
    m->dirPin = dir;
    m->enaPin = ena;
    m->reversed = rev;
    m->running = false;
    m->configured = true;
    m->position = 0;
    motorCount++;

    pinMode(pul, OUTPUT);
    pinMode(dir, OUTPUT);
    pinMode(ena, OUTPUT);
    digitalWrite(ena, HIGH);

    StaticJsonDocument<192> resp;
    resp["status"] = "motor_added";
    resp["name"] = name;
    resp["pul_pin"] = pul;
    resp["dir_pin"] = dir;
    resp["ena_pin"] = ena;
    resp["reversed"] = rev;
    resp["total_motors"] = motorCount;
    serializeJson(resp, Serial);
    Serial.println();

  // ── remove_motor ──
  } else if (strcmp(cmd, "remove_motor") == 0) {
    const char* name = doc["name"];
    if (!name) { sendError("need name"); return; }

    for (int i = 0; i < motorCount; i++) {
      if (motors[i].configured && strcmp(motors[i].name, name) == 0) {
        digitalWrite(motors[i].enaPin, HIGH);
        // Shift remaining motors down
        for (int j = i; j < motorCount - 1; j++) {
          motors[j] = motors[j + 1];
        }
        motors[motorCount - 1].configured = false;
        motorCount--;
        sendOk("motor_removed");
        return;
      }
    }
    sendError("motor not found");

  // ── list_motors ──
  } else if (strcmp(cmd, "list_motors") == 0) {
    StaticJsonDocument<512> resp;
    resp["status"] = "ok";
    JsonArray arr = resp.createNestedArray("motors");
    for (int i = 0; i < motorCount; i++) {
      if (!motors[i].configured) continue;
      JsonObject obj = arr.createNestedObject();
      obj["name"] = motors[i].name;
      obj["pul_pin"] = motors[i].pulPin;
      obj["dir_pin"] = motors[i].dirPin;
      obj["ena_pin"] = motors[i].enaPin;
      obj["reversed"] = motors[i].reversed;
      obj["running"] = motors[i].running;
      obj["position"] = motors[i].position;
    }
    serializeJson(resp, Serial);
    Serial.println();

  // ── run_motor ──
  } else if (strcmp(cmd, "run_motor") == 0) {
    const char* name = doc["name"];
    if (!name) { sendError("need name"); return; }

    Motor* m = findMotor(name);
    if (!m) { sendError("motor not found"); return; }
    if (anyMotorRunning()) { sendError("motor already running"); return; }

    long steps = doc["steps"] | 1000L;
    int speed_us = doc["speed_us"] | DEFAULT_SPEED_US;
    bool forward = doc["forward"] | true;

    Motor* selected[1] = {m};
    bool forwards[1] = {forward};
    startAsyncRunUniform(selected, forwards, 1, steps, speed_us);
    sendOk("started");

  // ── run_group ──
  } else if (strcmp(cmd, "run_group") == 0) {
    JsonArray names = doc["names"].as<JsonArray>();
    JsonArray motorsSpec = doc["motors"].as<JsonArray>();
    if ((names.isNull() || names.size() == 0) && (motorsSpec.isNull() || motorsSpec.size() == 0)) {
      sendError("need names or motors");
      return;
    }

    long groupSteps = doc["steps"] | 1000L;
    int groupSpeed = doc["speed_us"] | DEFAULT_SPEED_US;

    Motor* selected[MAX_MOTORS];
    bool forwards[MAX_MOTORS];
    int speeds[MAX_MOTORS];
    long targets[MAX_MOTORS];
    int selectedCount = 0;
    bool forward = doc["forward"] | true;

    if (anyMotorRunning()) {
      sendError("motor already running");
      return;
    }

    if (!motorsSpec.isNull() && motorsSpec.size() > 0) {
      for (JsonVariant value : motorsSpec) {
        JsonObject motorSpec = value.as<JsonObject>();
        const char* motorName = motorSpec["name"];
        bool motorForward = motorSpec["forward"] | true;
        if (!motorName) {
          sendError("invalid motor spec");
          return;
        }

        Motor* m = findMotor(motorName);
        if (!m) {
          sendError("motor not found");
          return;
        }

        selected[selectedCount] = m;
        forwards[selectedCount] = motorForward;
        // Per-motor overrides fall back to the group values.
        speeds[selectedCount] = motorSpec["speed_us"] | groupSpeed;
        targets[selectedCount] = motorSpec["steps"] | groupSteps;
        selectedCount++;
        if (selectedCount >= MAX_MOTORS) break;
      }
    } else {
      for (JsonVariant value : names) {
        const char* motorName = value.as<const char*>();
        if (!motorName) {
          sendError("invalid motor name");
          return;
        }

        Motor* m = findMotor(motorName);
        if (!m) {
          sendError("motor not found");
          return;
        }

        selected[selectedCount] = m;
        forwards[selectedCount] = forward;
        speeds[selectedCount] = groupSpeed;
        targets[selectedCount] = groupSteps;
        selectedCount++;
        if (selectedCount >= MAX_MOTORS) break;
      }
    }

    startAsyncRun(selected, forwards, speeds, targets, selectedCount);
    sendOk("started");

  // ── stop_motor ──
  } else if (strcmp(cmd, "stop_motor") == 0) {
    const char* name = doc["name"];
    if (!name) {
      // Stop all
      globalStop = true;
      if (asyncRunActive) {
        clearAsyncRun();
      }
      for (int i = 0; i < motorCount; i++) {
        if (motors[i].configured) stopMotorOutputState(&motors[i]);
      }
      sendOk("all_stopped");
    } else {
      Motor* m = findMotor(name);
      if (!m) { sendError("motor not found"); return; }
      globalStop = true;
      if (asyncRunActive) {
        clearAsyncRun();
      }
      stopMotorOutputState(m);
      sendOk("stopped");
    }

  // ── stop (global emergency) ──
  } else if (strcmp(cmd, "stop") == 0) {
    globalStop = true;
    if (asyncRunActive) {
      clearAsyncRun();
    }
    for (int i = 0; i < motorCount; i++) {
      if (motors[i].configured) {
        stopMotorOutputState(&motors[i]);
      }
    }
    sendOk("emergency_stop");

  // ── start (run all motors — pipeline compatibility) ──
  } else if (strcmp(cmd, "start") == 0) {
    if (motorCount == 0) {
      sendError("no motors configured");
      return;
    }
    int steps = doc["steps"] | 1000;
    int speed_us = doc["speed_us"] | DEFAULT_SPEED_US;
    bool forward = doc["forward"] | true;

    Motor* selected[MAX_MOTORS];
    bool forwards[MAX_MOTORS];
    int selectedCount = 0;
    for (int i = 0; i < motorCount; i++) {
      if (!motors[i].configured) continue;
      selected[selectedCount++] = &motors[i];
      forwards[selectedCount - 1] = forward;
    }

    globalStop = false;
    runMotorGroup(selected, forwards, selectedCount, steps, speed_us);

    if (globalStop) {
      sendOk("stopped");
    } else {
      sendOk("done");
    }

  // ── get_status ──
  } else if (strcmp(cmd, "get_status") == 0) {
    StaticJsonDocument<256> resp;
    resp["status"] = "ok";
    resp["id"] = stationId;
    resp["firmware"] = FIRMWARE_NAME;
    resp["version"] = FIRMWARE_VERSION;
    resp["build"] = FIRMWARE_BUILD;
    resp["motors"] = motorCount;
    resp["limits"] = limitCount;
    resp["encoders"] = encoderCount;

    bool anyRunning = false;
    for (int i = 0; i < motorCount; i++) {
      if (motors[i].running) anyRunning = true;
    }
    resp["state"] = anyRunning ? "PROCESSING" : "IDLE";
    serializeJson(resp, Serial);
    Serial.println();

  // ── verify_pin ──
  } else if (strcmp(cmd, "verify_pin") == 0) {
    int pin = doc["pin"] | -1;
    if (pin < 0) { sendError("need pin"); return; }
    const char* mode = doc["mode"] | "output";

    if (strcmp(mode, "output") == 0) {
      pinMode(pin, OUTPUT);
      digitalWrite(pin, HIGH);
      delay(200);
      digitalWrite(pin, LOW);
      sendOk("pin_toggled");
    } else if (strcmp(mode, "input") == 0) {
      pinMode(pin, INPUT_PULLUP);
      int val = digitalRead(pin);
      StaticJsonDocument<128> resp;
      resp["status"] = "ok";
      resp["pin"] = pin;
      resp["value"] = val;
      serializeJson(resp, Serial);
      Serial.println();
    } else {
      sendError("mode must be 'input' or 'output'");
    }

  // ── set_zero ──
  } else if (strcmp(cmd, "set_zero") == 0) {
    const char* name = doc["name"];
    if (!name) {
      for (int i = 0; i < motorCount; i++) motors[i].position = 0;
      sendOk("zeroed_all");
    } else {
      Motor* m = findMotor(name);
      if (!m) { sendError("motor not found"); return; }
      m->position = 0;
      sendOk("zeroed");
    }

  // ── get_position ──
  } else if (strcmp(cmd, "get_position") == 0) {
    const char* name = doc["name"];
    if (!name) { sendError("need name"); return; }
    Motor* m = findMotor(name);
    if (!m) { sendError("motor not found"); return; }
    StaticJsonDocument<128> resp;
    resp["status"] = "ok";
    resp["name"] = m->name;
    resp["position"] = m->position;
    resp["running"] = m->running;
    serializeJson(resp, Serial);
    Serial.println();

  // ── add_limit ──
  } else if (strcmp(cmd, "add_limit") == 0) {
    if (limitCount >= MAX_LIMITS) { sendError("max limits reached"); return; }
    const char* name = doc["name"];
    int pin = doc["pin"] | -1;
    bool no = doc["normally_open"] | true;
    if (!name || pin < 0) { sendError("need name, pin"); return; }
    if (findLimit(name)) { sendError("limit name already exists"); return; }

    LimitSwitch* lim = &limits[limitCount];
    strncpy(lim->name, name, 15);
    lim->name[15] = '\0';
    lim->pin = pin;
    lim->normallyOpen = no;
    lim->configured = true;
    lim->tripped = false;
    lim->lastActive = false;
    lim->stopCount = 0;

    JsonArray stops = doc["stops"].as<JsonArray>();
    if (!stops.isNull()) {
      for (JsonVariant v : stops) {
        const char* mn = v.as<const char*>();
        if (mn && lim->stopCount < MAX_STOPS) {
          strncpy(lim->stopNames[lim->stopCount], mn, 15);
          lim->stopNames[lim->stopCount][15] = '\0';
          lim->stopCount++;
        }
      }
    }

    pinMode(pin, no ? INPUT_PULLUP : INPUT_PULLUP);
    limitCount++;

    StaticJsonDocument<192> resp;
    resp["status"] = "limit_added";
    resp["name"] = name;
    resp["pin"] = pin;
    resp["total_limits"] = limitCount;
    serializeJson(resp, Serial);
    Serial.println();

  // ── remove_limit ──
  } else if (strcmp(cmd, "remove_limit") == 0) {
    const char* name = doc["name"];
    if (!name) { sendError("need name"); return; }
    for (int i = 0; i < limitCount; i++) {
      if (limits[i].configured && strcmp(limits[i].name, name) == 0) {
        for (int j = i; j < limitCount - 1; j++) limits[j] = limits[j + 1];
        limits[limitCount - 1].configured = false;
        limitCount--;
        sendOk("limit_removed");
        return;
      }
    }
    sendError("limit not found");

  // ── list_limits ──
  } else if (strcmp(cmd, "list_limits") == 0) {
    StaticJsonDocument<512> resp;
    resp["status"] = "ok";
    JsonArray arr = resp.createNestedArray("limits");
    for (int i = 0; i < limitCount; i++) {
      if (!limits[i].configured) continue;
      JsonObject obj = arr.createNestedObject();
      obj["name"] = limits[i].name;
      obj["pin"] = limits[i].pin;
      obj["normally_open"] = limits[i].normallyOpen;
      obj["tripped"] = limits[i].tripped;
      obj["stops"] = limits[i].stopCount;
    }
    serializeJson(resp, Serial);
    Serial.println();

  // ── clear_limit ──
  } else if (strcmp(cmd, "clear_limit") == 0) {
    const char* name = doc["name"];
    if (!name) {
      for (int i = 0; i < limitCount; i++) limits[i].tripped = false;
      sendOk("limits_cleared");
    } else {
      LimitSwitch* lim = findLimit(name);
      if (!lim) { sendError("limit not found"); return; }
      lim->tripped = false;
      sendOk("limit_cleared");
    }

  // ── add_encoder (reserved) ──
  } else if (strcmp(cmd, "add_encoder") == 0) {
    if (encoderCount >= MAX_ENCODERS) { sendError("max encoders reached"); return; }
    const char* name = doc["name"];
    int pinA = doc["pin_a"] | -1;
    int pinB = doc["pin_b"] | -1;
    long cpr = doc["counts_per_rev"] | 0;
    if (!name || pinA < 0) { sendError("need name, pin_a"); return; }
    if (findEncoder(name)) { sendError("encoder name already exists"); return; }

    Encoder* enc = &encoders[encoderCount];
    strncpy(enc->name, name, 15);
    enc->name[15] = '\0';
    const char* mn = doc["motor"] | "";
    strncpy(enc->motor, mn, 15);
    enc->motor[15] = '\0';
    enc->pinA = pinA;
    enc->pinB = pinB;
    enc->countsPerRev = cpr;
    enc->count = 0;
    enc->configured = true;
    pinMode(pinA, INPUT_PULLUP);
    if (pinB >= 0) pinMode(pinB, INPUT_PULLUP);
    encoderCount++;
    sendOk("encoder_added");

  // ── remove_encoder ──
  } else if (strcmp(cmd, "remove_encoder") == 0) {
    const char* name = doc["name"];
    if (!name) { sendError("need name"); return; }
    for (int i = 0; i < encoderCount; i++) {
      if (encoders[i].configured && strcmp(encoders[i].name, name) == 0) {
        for (int j = i; j < encoderCount - 1; j++) encoders[j] = encoders[j + 1];
        encoders[encoderCount - 1].configured = false;
        encoderCount--;
        sendOk("encoder_removed");
        return;
      }
    }
    sendError("encoder not found");

  // ── list_encoders ──
  } else if (strcmp(cmd, "list_encoders") == 0) {
    StaticJsonDocument<512> resp;
    resp["status"] = "ok";
    JsonArray arr = resp.createNestedArray("encoders");
    for (int i = 0; i < encoderCount; i++) {
      if (!encoders[i].configured) continue;
      JsonObject obj = arr.createNestedObject();
      obj["name"] = encoders[i].name;
      obj["motor"] = encoders[i].motor;
      obj["pin_a"] = encoders[i].pinA;
      obj["pin_b"] = encoders[i].pinB;
      obj["count"] = encoders[i].count;
    }
    serializeJson(resp, Serial);
    Serial.println();

  // ── get_encoder ──
  } else if (strcmp(cmd, "get_encoder") == 0) {
    const char* name = doc["name"];
    if (!name) { sendError("need name"); return; }
    Encoder* enc = findEncoder(name);
    if (!enc) { sendError("encoder not found"); return; }
    StaticJsonDocument<128> resp;
    resp["status"] = "ok";
    resp["name"] = enc->name;
    resp["count"] = enc->count;
    serializeJson(resp, Serial);
    Serial.println();

  // ── reset_encoder ──
  } else if (strcmp(cmd, "reset_encoder") == 0) {
    const char* name = doc["name"];
    if (!name) { sendError("need name"); return; }
    Encoder* enc = findEncoder(name);
    if (!enc) { sendError("encoder not found"); return; }
    enc->count = 0;
    sendOk("encoder_reset");

  } else {
    sendError("unknown command");
  }
}
