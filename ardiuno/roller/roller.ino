const int pulPin = 9;   // PUL+ (D9)
const int dirPin = 8;   // DIR+ (D8)
const int enaPin = 7;   // ENA+ (D7)
const int beamPin = 2;  // Beam sensor on D2 (interrupt-capable)

volatile int beamState = HIGH;          // Current state of the beam
volatile unsigned long beamToggleCount = 0;  // Total number of beam toggles
volatile bool beamChanged = false;      // Flag to notify main loop

void setup() {
  // Set up motor pins
  pinMode(pulPin, OUTPUT);
  pinMode(dirPin, OUTPUT);
  pinMode(enaPin, OUTPUT);
  digitalWrite(enaPin, LOW);
  digitalWrite(dirPin, HIGH);

  // Set up beam sensor
  pinMode(beamPin, INPUT_PULLUP);  // Use pull-up
  attachInterrupt(digitalPinToInterrupt(beamPin), beamISR, CHANGE);  // Respond to any state change

  // Serial for debug and control
  Serial.begin(9600);
  while (!Serial) {}
  Serial.println("Stepper + Beam Sensor Interrupt Ready. Send input to trigger motor.");
}

void loop() {
  // Beam change output
  if (beamChanged) {
    beamChanged = false;
    if (beamState == LOW) {
      Serial.print("Beam broken! ");
    } else {
      Serial.print("Beam restored. ");
    }
    Serial.print("Toggles: ");
    Serial.println(beamToggleCount);
  }

  // Serial trigger for motor
  if (Serial.available() > 0) {
    while (Serial.available() > 0) {
      Serial.read();
    }

    Serial.println("Input received! Starting 1-second motor turn...");

    digitalWrite(enaPin, LOW);

    int stepsForOneSecond = 5000;

    for (int i = 0; i < stepsForOneSecond; i++) {
      digitalWrite(pulPin, HIGH);
      delayMicroseconds(500);
      digitalWrite(pulPin, LOW);
      delayMicroseconds(500);
    }

    Serial.println("Motor turn complete.");
  }
}

// Interrupt Service Routine
void beamISR() {
  beamState = digitalRead(beamPin);  // Read the new state
  beamToggleCount++;                 // Count toggle
  beamChanged = true;               // Notify main loop to print
}
