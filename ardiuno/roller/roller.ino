#include <ArduinoJson.h>

const int pulPin = 9;
const int dirPin = 8;
const int enaPin = 7;
const int beamPin = 2;

volatile int beamState = HIGH;
volatile unsigned long beamToggleCount = 0;
volatile bool beamChanged = false;
volatile bool stopRequested = false;

String currentState = "IDLE";

void setup() {
  pinMode(pulPin, OUTPUT);
  pinMode(dirPin, OUTPUT);
  pinMode(enaPin, OUTPUT);
  digitalWrite(enaPin, HIGH);  // Disabled until needed
  digitalWrite(dirPin, HIGH);

  pinMode(beamPin, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(beamPin), beamISR, CHANGE);

  Serial.begin(9600);
  while (!Serial) {}
}

void sendResponse(const char* status, JsonObject data = JsonObject()) {
  StaticJsonDocument<256> resp;
  resp["status"] = status;
  if (!data.isNull()) {
    resp["data"] = data;
  }
  serializeJson(resp, Serial);
  Serial.println();
}

void loop() {
  if (Serial.available()) {
    StaticJsonDocument<200> doc;
    DeserializationError err = deserializeJson(doc, Serial);
    if (err) return;

    const char* cmd = doc["cmd"];
    if (!cmd) return;

    if (strcmp(cmd, "identify") == 0) {
      StaticJsonDocument<128> resp;
      resp["id"] = "roller";
      resp["version"] = "1.0";
      serializeJson(resp, Serial);
      Serial.println();

    } else if (strcmp(cmd, "get_status") == 0) {
      StaticJsonDocument<256> resp;
      resp["status"] = currentState;
      resp["beam_state"] = (beamState == LOW) ? "broken" : "clear";
      resp["beam_count"] = beamToggleCount;
      serializeJson(resp, Serial);
      Serial.println();

    } else if (strcmp(cmd, "start") == 0) {
      int steps = doc["steps"] | 1000;  // Default 1000 steps (~1 second)
      int speed_us = doc["speed_us"] | 500;

      currentState = "PROCESSING";
      stopRequested = false;
      digitalWrite(enaPin, LOW);

      for (int i = 0; i < steps; i++) {
        if (stopRequested) break;
        digitalWrite(pulPin, HIGH);
        delayMicroseconds(speed_us);
        digitalWrite(pulPin, LOW);
        delayMicroseconds(speed_us);
      }

      digitalWrite(enaPin, HIGH);

      if (stopRequested) {
        currentState = "IDLE";
        sendResponse("stopped");
      } else {
        currentState = "IDLE";
        sendResponse("done");
      }

    } else if (strcmp(cmd, "stop") == 0) {
      stopRequested = true;
      digitalWrite(enaPin, HIGH);
      currentState = "IDLE";
      sendResponse("stopped");
    }
  }
}

void beamISR() {
  beamState = digitalRead(beamPin);
  beamToggleCount++;
  beamChanged = true;
}
