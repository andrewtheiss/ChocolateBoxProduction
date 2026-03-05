#include <ArduinoJson.h>

const int STEP_PIN = 9;
const int DIR_PIN = 8;
const int EN_PIN = 7;
const int SENSOR_PIN = 2;

volatile bool stopRequested = false;
String currentState = "IDLE";

void setup() {
  Serial.begin(9600);
  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(EN_PIN, OUTPUT);
  pinMode(SENSOR_PIN, INPUT);
  digitalWrite(EN_PIN, HIGH);  // Disabled until needed
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
      resp["id"] = "dispenser";
      resp["version"] = "1.0";
      serializeJson(resp, Serial);
      Serial.println();

    } else if (strcmp(cmd, "get_status") == 0) {
      StaticJsonDocument<256> resp;
      resp["status"] = currentState;
      resp["sensor"] = (digitalRead(SENSOR_PIN) == LOW) ? "triggered" : "clear";
      serializeJson(resp, Serial);
      Serial.println();

    } else if (strcmp(cmd, "start") == 0) {
      int steps = doc["steps"] | 200;
      int speed_us = doc["speed_us"] | 500;

      currentState = "PROCESSING";
      stopRequested = false;
      digitalWrite(DIR_PIN, HIGH);
      digitalWrite(EN_PIN, LOW);

      for (int i = 0; i < steps; i++) {
        if (stopRequested) break;
        digitalWrite(STEP_PIN, HIGH);
        delayMicroseconds(speed_us);
        digitalWrite(STEP_PIN, LOW);
        delayMicroseconds(speed_us);
      }

      digitalWrite(EN_PIN, HIGH);

      if (stopRequested) {
        currentState = "IDLE";
        sendResponse("stopped");
      } else {
        bool sensorTriggered = (digitalRead(SENSOR_PIN) == LOW);
        currentState = "IDLE";
        if (sensorTriggered) {
          sendResponse("done");
        } else {
          sendResponse("stack_empty");
        }
      }

    } else if (strcmp(cmd, "stop") == 0) {
      stopRequested = true;
      digitalWrite(EN_PIN, HIGH);
      currentState = "IDLE";
      sendResponse("stopped");
    }
  }
}
