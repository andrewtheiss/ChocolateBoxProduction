#include <ArduinoJson.h>

// ── Motor registry ──────────────────────────
#define MAX_MOTORS 6

struct Motor {
  char name[16];
  int pulPin;
  int dirPin;
  int enaPin;
  bool reversed;   // swap direction logic
  bool running;
  bool configured;
};

Motor motors[MAX_MOTORS];
int motorCount = 0;
volatile bool globalStop = false;
String stationId = "generic";

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

void runSingleMotor(Motor* m, int steps, int speed_us, bool forward) {
  bool dir = forward;
  if (m->reversed) dir = !dir;

  digitalWrite(m->dirPin, dir ? HIGH : LOW);
  digitalWrite(m->enaPin, LOW);
  m->running = true;

  for (int i = 0; i < steps; i++) {
    if (globalStop) break;
    digitalWrite(m->pulPin, HIGH);
    delayMicroseconds(speed_us);
    digitalWrite(m->pulPin, LOW);
    delayMicroseconds(speed_us);
  }

  digitalWrite(m->enaPin, HIGH);
  m->running = false;
}

// ── Main ────────────────────────────────────
void setup() {
  Serial.begin(9600);
  while (!Serial) {}

  for (int i = 0; i < MAX_MOTORS; i++) {
    motors[i].configured = false;
  }
}

void loop() {
  if (!Serial.available()) return;

  StaticJsonDocument<384> doc;
  DeserializationError err = deserializeJson(doc, Serial);
  if (err) return;

  const char* cmd = doc["cmd"];
  if (!cmd) return;

  // ── identify ──
  if (strcmp(cmd, "identify") == 0) {
    StaticJsonDocument<128> resp;
    resp["id"] = stationId;
    resp["version"] = "2.0";
    resp["motors"] = motorCount;
    serializeJson(resp, Serial);
    Serial.println();

  // ── set_id ──
  } else if (strcmp(cmd, "set_id") == 0) {
    const char* newId = doc["id"];
    if (newId) {
      stationId = String(newId);
      sendOk("id_set");
    } else {
      sendError("missing id");
    }

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
    }
    serializeJson(resp, Serial);
    Serial.println();

  // ── run_motor ──
  } else if (strcmp(cmd, "run_motor") == 0) {
    const char* name = doc["name"];
    if (!name) { sendError("need name"); return; }

    Motor* m = findMotor(name);
    if (!m) { sendError("motor not found"); return; }
    if (m->running) { sendError("motor already running"); return; }

    int steps = doc["steps"] | 1000;
    int speed_us = doc["speed_us"] | 500;
    bool forward = doc["forward"] | true;

    globalStop = false;
    runSingleMotor(m, steps, speed_us, forward);

    if (globalStop) {
      sendOk("stopped");
    } else {
      sendOk("done");
    }

  // ── stop_motor ──
  } else if (strcmp(cmd, "stop_motor") == 0) {
    const char* name = doc["name"];
    if (!name) {
      // Stop all
      globalStop = true;
      for (int i = 0; i < motorCount; i++) {
        if (motors[i].configured) {
          digitalWrite(motors[i].enaPin, HIGH);
          motors[i].running = false;
        }
      }
      sendOk("all_stopped");
    } else {
      Motor* m = findMotor(name);
      if (!m) { sendError("motor not found"); return; }
      globalStop = true;
      digitalWrite(m->enaPin, HIGH);
      m->running = false;
      sendOk("stopped");
    }

  // ── stop (global emergency) ──
  } else if (strcmp(cmd, "stop") == 0) {
    globalStop = true;
    for (int i = 0; i < motorCount; i++) {
      if (motors[i].configured) {
        digitalWrite(motors[i].enaPin, HIGH);
        motors[i].running = false;
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
    int speed_us = doc["speed_us"] | 500;
    bool forward = doc["forward"] | true;

    globalStop = false;
    for (int i = 0; i < motorCount; i++) {
      if (!motors[i].configured) continue;
      runSingleMotor(&motors[i], steps, speed_us, forward);
      if (globalStop) break;
    }

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
    resp["motors"] = motorCount;

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

  } else {
    sendError("unknown command");
  }
}
