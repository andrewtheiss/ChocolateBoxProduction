#include <ArduinoJson.h>  // Install via Library Manager for JSON

// Pins: Adjust for your setup
const int STEP_PIN = 9;
const int DIR_PIN = 8;
const int EN_PIN = 7;
const int SENSOR_PIN = 2;  // e.g., IR sensor

void setup() {
  Serial.begin(9600);
  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(EN_PIN, OUTPUT);
  pinMode(SENSOR_PIN, INPUT);
  digitalWrite(EN_PIN, LOW);  // Enable motor
}

void loop() {
  if (Serial.available()) {
    StaticJsonDocument<200> doc;
    deserializeJson(doc, Serial);
    String cmd = doc["cmd"];

    if (cmd == "start_dispense") {
      // Dispense logic: e.g., step motor 200 times
      digitalWrite(DIR_PIN, HIGH);
      for (int i = 0; i < 200; i++) {
        digitalWrite(STEP_PIN, HIGH);
        delayMicroseconds(500);
        digitalWrite(STEP_PIN, LOW);
        delayMicroseconds(500);
      }
      // Check sensor
      if (digitalRead(SENSOR_PIN) == LOW) {  // Assume LOW = item dispensed
        Serial.println("{\"status\":\"done\"}");
      } else {
        Serial.println("{\"status\":\"stack_empty\"}");
      }
    } else if (cmd == "get_status") {
      Serial.println("{\"status\":\"IDLE\"}");  // Or read actual
    }
  }
}