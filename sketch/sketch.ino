#include <Arduino.h>
#include <SensirionI2cSen66.h>
#include <Wire.h>
#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>

#ifdef NO_ERROR
#undef NO_ERROR
#endif
#define NO_ERROR 0

SensirionI2cSen66 sensor;
Arduino_LED_Matrix matrix;

static char errorMessage[64];
static int16_t error;

// Buzzer and alarm state

const int BUZZER_PIN = 9;

// These values must match the alarm modes in python.
const int ALARM_OFF = 0;
const int ALARM_PM1 = 1;
const int ALARM_PM25 = 2;
const int ALARM_PM4 = 3;
const int ALARM_PM10 = 4;
const int ALARM_VOC = 5;
const int ALARM_MULTIPLE = 6;
const int ALARM_CO2 = 7;
const int ALARM_NOX = 8;

volatile int currentAlarmMode = ALARM_OFF;
volatile int requestedPpeAlarmMode = ALARM_OFF;

unsigned long alarmTimer = 0;
int alarmStep = 0;

// SEN66 hazard limits

const float PM1_TRIGGER = 20.0;
const float PM25_TRIGGER = 40.0;
const float PM4_TRIGGER = 70;
const float PM10_TRIGGER = 100.0;

const int VOC_TRIGGER = 2000;
const int NOX_TRIGGER = 100;

const uint16_t CO2_TRIGGER = 3000;

// Start PPE detection after 3 hazardous readings and stop it after 5 safe ones.

const int REQUIRED_DANGER_READINGS = 3;
const int REQUIRED_SAFE_READINGS = 5;

int dangerCount = 0;
int safeCount = 0;

bool yoloTriggered = false;

int co2DangerCount = 0;
int co2SafeCount = 0;
bool co2AlarmActive = false;

int noxDangerCount = 0;
int noxSafeCount = 0;
bool noxAlarmActive = false;

// Ignore hazard decisions while the SEN66 completes its 30-second warm-up.

const unsigned long SENSOR_WARMUP_TIME = 30000;

unsigned long sensorStartTime = 0;
unsigned long lastMatrixUpdate = 0;
bool warmupDisplayFinished = false;

// Images for the 8-by-13 LED matrix

// Check mark: no hazards detected
uint8_t readyFrame[104] = {

    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0,
    0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0};

// "PM": particulate matter hazard.
uint8_t pmFrame[104] = {

    1, 1, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0, 0, 0,
    1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 1, 1, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0,
    1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0,
    1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0};

// "VO": VOC hazard.
uint8_t vocFrame[104] = {

    1, 0, 0, 0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0,
    1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0,
    1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0,
    0, 1, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0};

// Warning triangle: multiple hazards are active.
uint8_t multipleFrame[104] = {

    0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 0,
    0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
    1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1};

// Fill the matrix from left to right as the sensor warms up.
void showWarmupProgress(unsigned long elapsedMs) {
  uint8_t frame[104] = {0};

  int ledsToLight = (elapsedMs * 104UL) / SENSOR_WARMUP_TIME;

  if (ledsToLight > 104) {
    ledsToLight = 104;
  }

  for (int i = 0; i < ledsToLight; i++) {
    frame[i] = 1;
  }

  matrix.draw(frame);
}

// The buzzer is active-high, so HIGH turns it on and LOW turns it off.
void buzzerHardware(bool on) { digitalWrite(BUZZER_PIN, on ? HIGH : LOW); }

// Apply the highest-priority alarm that is currently active.
void refreshAlarmMode() {
  int alarmMode = requestedPpeAlarmMode;

  if (co2AlarmActive) {
    alarmMode = ALARM_CO2;
  }

  if (noxAlarmActive) {
    alarmMode = ALARM_NOX;
  }

  if (alarmMode == currentAlarmMode) {
    return;
  }

  currentAlarmMode = alarmMode;

  alarmStep = 0;
  alarmTimer = millis();

  // Begin each new pattern with the buzzer off.
  buzzerHardware(false);

  if (alarmMode == ALARM_OFF) {
    matrix.draw(readyFrame);
  }

  else if (alarmMode == ALARM_PM1 || alarmMode == ALARM_PM25 ||
           alarmMode == ALARM_PM4 || alarmMode == ALARM_PM10) {
    matrix.draw(pmFrame);
  }

  else if (alarmMode == ALARM_VOC) {
    matrix.draw(vocFrame);
  }

  else if (alarmMode == ALARM_MULTIPLE || alarmMode == ALARM_CO2 ||
           alarmMode == ALARM_NOX) {
    matrix.draw(multipleFrame);
  }
}

// Called by python/main.py through Bridge.notify("set_alarm", mode).
void setAlarm(int alarmMode) {
  requestedPpeAlarmMode = alarmMode;
  refreshAlarmMode();
}

// Require several consecutive readings before changing an environmental alarm.
void updateEnvironmentalAlarm(bool hazard, int& dangerReadings, int& safeReadings,
                              bool& alarmActive) {
  if (hazard) {
    dangerReadings++;
    safeReadings = 0;

    if (dangerReadings >= REQUIRED_DANGER_READINGS) {
      alarmActive = true;
      dangerReadings = 0;
    }
  } else {
    dangerReadings = 0;

    if (alarmActive) {
      safeReadings++;

      if (safeReadings >= REQUIRED_SAFE_READINGS) {
        alarmActive = false;
        safeReadings = 0;
      }
    }
  }
}

// Update the buzzer without using delay(), so sensor reads and Bridge messages
// can continue while an alarm is sounding.
void updateAlarm() {
  if (currentAlarmMode == ALARM_OFF) {
    buzzerHardware(false);
    return;
  }

  unsigned long now = millis();

  // Smaller particles use a faster repeating beep.
  if (currentAlarmMode >= ALARM_PM1 && currentAlarmMode <= ALARM_PM10) {
    unsigned long beepTime = 250;
    unsigned long pauseTime = 1250;

    if (currentAlarmMode == ALARM_PM1) {
      beepTime = 100;
      pauseTime = 100;
    } else if (currentAlarmMode == ALARM_PM25) {
      beepTime = 200;
      pauseTime = 300;
    } else if (currentAlarmMode == ALARM_PM4) {
      beepTime = 250;
      pauseTime = 750;
    }

    if (alarmStep == 0) {
      buzzerHardware(true);

      if (now - alarmTimer >= beepTime) {
        alarmStep = 1;
        alarmTimer = now;
      }
    }

    else {
      buzzerHardware(false);

      if (now - alarmTimer >= pauseTime) {
        alarmStep = 0;
        alarmTimer = now;
      }
    }
  }

  // VOC alarm: two short beeps followed by a pause.
  else if (currentAlarmMode == ALARM_VOC) {
    switch (alarmStep) {
      case 0:

        buzzerHardware(true);

        if (now - alarmTimer >= 180) {
          alarmStep = 1;
          alarmTimer = now;
        }

        break;

      case 1:

        buzzerHardware(false);

        if (now - alarmTimer >= 150) {
          alarmStep = 2;
          alarmTimer = now;
        }

        break;

      case 2:

        buzzerHardware(true);

        if (now - alarmTimer >= 180) {
          alarmStep = 3;
          alarmTimer = now;
        }

        break;

      default:

        buzzerHardware(false);

        if (now - alarmTimer >= 800) {
          alarmStep = 0;
          alarmTimer = now;
        }

        break;
    }
  }

  // Multiple hazards: three quick beeps followed by a pause.
  else if (currentAlarmMode == ALARM_MULTIPLE) {
    switch (alarmStep) {
      case 0:
      case 2:
      case 4:

        buzzerHardware(true);

        if (now - alarmTimer >= 120) {
          alarmStep++;
          alarmTimer = now;
        }

        break;

      case 1:
      case 3:

        buzzerHardware(false);

        if (now - alarmTimer >= 100) {
          alarmStep++;
          alarmTimer = now;
        }

        break;

      default:

        buzzerHardware(false);

        if (now - alarmTimer >= 600) {
          alarmStep = 0;
          alarmTimer = now;
        }

        break;
    }
  }

  // CO2 alarm: a long, widely spaced reminder to increase airflow.
  else if (currentAlarmMode == ALARM_CO2) {
    if (alarmStep == 0) {
      buzzerHardware(true);

      if (now - alarmTimer >= 500) {
        alarmStep = 1;
        alarmTimer = now;
      }
    } else {
      buzzerHardware(false);

      if (now - alarmTimer >= 1500) {
        alarmStep = 0;
        alarmTimer = now;
      }
    }
  }

  // NOx alarm: an urgent, rapid warning to leave the area.
  else if (currentAlarmMode == ALARM_NOX) {
    if (alarmStep == 0) {
      buzzerHardware(true);

      if (now - alarmTimer >= 200) {
        alarmStep = 1;
        alarmTimer = now;
      }
    } else {
      buzzerHardware(false);

      if (now - alarmTimer >= 100) {
        alarmStep = 0;
        alarmTimer = now;
      }
    }
  }
}

void setup() {
  Serial.begin(115200);

  Bridge.begin();

  // Set the buzzer HIGH before making the pin an output so it starts silently.
  digitalWrite(BUZZER_PIN, HIGH);

  pinMode(BUZZER_PIN, OUTPUT);

  buzzerHardware(false);

  // The Python app chooses a complete alarm pattern, not individual beeps.
  Bridge.provide_safe("set_alarm", setAlarm);

  // The frames only use on/off pixels, so one grayscale bit is enough.
  matrix.begin();

  matrix.setGrayscaleBits(1);

  matrix.clear();

  // Start continuous SEN66 measurements on the Wire1 I2C bus.
  Wire1.begin();

  sensor.begin(Wire1, SEN66_I2C_ADDR_6B);

  error = sensor.stopMeasurement();

  if (error != NO_ERROR) {
    Serial.println("Stop measurement not successful");
  }

  delay(50);

  error = sensor.startContinuousMeasurement();

  if (error != NO_ERROR) {
    Serial.println("Start measurement not successful");
  }

  sensorStartTime = millis();

  Serial.println();
  Serial.println("SEN66 started.");
  Serial.println("30 second warm-up started.");
  Serial.println();
}

void loop() {
  // Advance the current buzzer pattern on every pass through loop().
  updateAlarm();

  uint8_t padding = 0;

  bool dataReady = false;

  uint16_t massConcentrationPm1p0 = 0;
  uint16_t massConcentrationPm2p5 = 0;
  uint16_t massConcentrationPm4p0 = 0;
  uint16_t massConcentrationPm10p0 = 0;

  int16_t ambientHumidity = 0;
  int16_t ambientTemperature = 0;

  int16_t vOCIndex = 0;
  int16_t nOxIndex = 0;

  uint16_t cO2 = 0;

  // Show warm-up progress, but limit matrix updates to four per second.
  unsigned long elapsedTime = millis() - sensorStartTime;

  bool sensorWarmedUp = elapsedTime >= SENSOR_WARMUP_TIME;

  if (!sensorWarmedUp) {
    if (millis() - lastMatrixUpdate >= 250) {
      lastMatrixUpdate = millis();

      showWarmupProgress(elapsedTime);
    }
  } else if (!warmupDisplayFinished) {
    matrix.draw(readyFrame);
    warmupDisplayFinished = true;
  }

  // There is nothing else to do until the sensor has a fresh sample.
  error = sensor.getDataReady(padding, dataReady);

  if (error != NO_ERROR) {
    Serial.print("getDataReady error: ");

    errorToString(error, errorMessage, sizeof errorMessage);

    Serial.println(errorMessage);

    return;
  }

  if (!dataReady) {
    return;
  }

  // Read the latest sample as scaled integers.
  error = sensor.readMeasuredValuesAsIntegers(

      massConcentrationPm1p0, massConcentrationPm2p5, massConcentrationPm4p0,
      massConcentrationPm10p0,

      ambientHumidity, ambientTemperature,

      vOCIndex, nOxIndex,

      cO2);

  if (error != NO_ERROR) {
    Serial.print("SEN66 read error: ");

    errorToString(error, errorMessage, sizeof errorMessage);

    Serial.println(errorMessage);

    return;
  }

  // Convert the scaled integers into the units used by the logger and limits.
  float pm1 = massConcentrationPm1p0 / 10.0;

  float pm25 = massConcentrationPm2p5 / 10.0;

  float pm4 = massConcentrationPm4p0 / 10.0;

  float pm10 = massConcentrationPm10p0 / 10.0;

  float humidity = ambientHumidity / 100.0;

  float temperature = ambientTemperature / 200.0;

  int vocIndex = vOCIndex;
  int noxIndex = nOxIndex;
  uint16_t co2 = cO2;

  // Send every sample to Python as one comma-separated Bridge message.
  String sensorData = "";

  sensorData += String(pm1, 1);
  sensorData += ",";

  sensorData += String(pm25, 1);
  sensorData += ",";

  sensorData += String(pm4, 1);
  sensorData += ",";

  sensorData += String(pm10, 1);
  sensorData += ",";

  sensorData += String(humidity, 1);
  sensorData += ",";

  sensorData += String(temperature, 1);
  sensorData += ",";

  sensorData += String(vocIndex);
  sensorData += ",";

  sensorData += String(noxIndex);
  sensorData += ",";

  sensorData += String(co2);

  Bridge.notify("sen66_reading", sensorData);

  // Check each measurement against its hazard limit.
  bool pm1Hazard = pm1 >= PM1_TRIGGER;
  bool pm25Hazard = pm25 >= PM25_TRIGGER;
  bool pm4Hazard = pm4 >= PM4_TRIGGER;
  bool pm10Hazard = pm10 >= PM10_TRIGGER;

  bool particulateHazard = pm1Hazard || pm25Hazard || pm4Hazard || pm10Hazard;

  bool vocHazard = vocIndex >= VOC_TRIGGER;

  bool noxHazard = noxIndex >= NOX_TRIGGER;

  bool co2Hazard = co2 >= CO2_TRIGGER;

  // Only particulate matter and VOC require camera-based PPE checks.
  bool ppeDangerCondition = particulateHazard || vocHazard;

  // Count consecutive danger or safe readings to avoid reacting to one spike.
  if (!sensorWarmedUp) {
    dangerCount = 0;
    safeCount = 0;

    co2DangerCount = 0;
    co2SafeCount = 0;
    co2AlarmActive = false;

    noxDangerCount = 0;
    noxSafeCount = 0;
    noxAlarmActive = false;

    setAlarm(ALARM_OFF);

  }

  else {
    bool previousCo2Alarm = co2AlarmActive;
    bool previousNoxAlarm = noxAlarmActive;

    updateEnvironmentalAlarm(co2Hazard, co2DangerCount, co2SafeCount, co2AlarmActive);
    updateEnvironmentalAlarm(noxHazard, noxDangerCount, noxSafeCount, noxAlarmActive);
    refreshAlarmMode();

    if (co2AlarmActive != previousCo2Alarm) {
      Serial.println(co2AlarmActive ? "CO2 ALARM: INCREASE AIRFLOW"
                                    : "CO2 levels returned to safe");
    }

    if (noxAlarmActive != previousNoxAlarm) {
      Serial.println(noxAlarmActive ? "NOX ALARM: LEAVE THE AREA"
                                    : "NOx levels returned to safe");
    }

    if (ppeDangerCondition) {
      dangerCount++;
      safeCount = 0;

    }

    else {
      dangerCount = 0;

      if (yoloTriggered) {
        safeCount++;
      }
    }
  }

  // Ask Python to start PPE detection after enough dangerous readings.
  if (sensorWarmedUp &&

      dangerCount >= REQUIRED_DANGER_READINGS &&

      !yoloTriggered) {
    String activeHazards = "";

    if (pm1Hazard) {
      activeHazards += "PM1";
    }

    if (pm25Hazard) {
      if (activeHazards.length() > 0) {
        activeHazards += ",";
      }
      activeHazards += "PM2.5";
    }

    if (pm4Hazard) {
      if (activeHazards.length() > 0) {
        activeHazards += ",";
      }
      activeHazards += "PM4";
    }

    if (pm10Hazard) {
      if (activeHazards.length() > 0) {
        activeHazards += ",";
      }
      activeHazards += "PM10";
    }

    if (vocHazard) {
      if (activeHazards.length() > 0) {
        activeHazards += ",";
      }

      activeHazards += "VOC";
    }

    Bridge.notify("start_yolo", activeHazards);

    yoloTriggered = true;

    dangerCount = 0;
    safeCount = 0;

    // Show the hazard now. Python decides whether missing PPE requires sound.

    if (particulateHazard && vocHazard) {
      matrix.draw(multipleFrame);
    }

    else if (vocHazard) {
      matrix.draw(vocFrame);
    }

    else {
      matrix.draw(pmFrame);
    }

    Serial.println();
    Serial.println("### PPE DETECTION TRIGGER SENT ###");

    Serial.print("Active hazards: ");

    Serial.println(activeHazards);
  }

  // Stop PPE detection once the sensor has remained safe long enough.
  if (sensorWarmedUp &&

      safeCount >= REQUIRED_SAFE_READINGS &&

      yoloTriggered) {
    Bridge.notify("stop_yolo", true);

    yoloTriggered = false;

    dangerCount = 0;
    safeCount = 0;

    // A safe state always silences the buzzer.
    setAlarm(ALARM_OFF);

    if (!co2AlarmActive && !noxAlarmActive) {
      matrix.draw(readyFrame);
    }

    Serial.println();
    Serial.println("### PPE DETECTION STOP SENT ###");
  }

  // Print a readable snapshot for `arduino-app-cli monitor`.
  Serial.println("-----------------------------");

  Serial.print("PM1: ");
  Serial.println(pm1);

  Serial.print("PM2.5: ");
  Serial.println(pm25);

  Serial.print("PM4: ");
  Serial.println(pm4);

  Serial.print("PM10: ");
  Serial.println(pm10);

  Serial.print("VOC: ");
  Serial.println(vocIndex);

  Serial.print("NOX: ");
  Serial.println(noxIndex);

  Serial.print("CO2: ");
  Serial.println(co2);

  if (sensorWarmedUp) {
    Serial.println("ACTIVE HAZARDS:");

    bool anyHazard = false;

    if (pm1Hazard) {
      Serial.println(" - PM1");

      anyHazard = true;
    }

    if (pm25Hazard) {
      Serial.println(" - PM2.5");

      anyHazard = true;
    }

    if (pm4Hazard) {
      Serial.println(" - PM4");

      anyHazard = true;
    }

    if (pm10Hazard) {
      Serial.println(" - PM10");

      anyHazard = true;
    }

    if (vocHazard) {
      Serial.println(" - VOC");

      anyHazard = true;
    }

    if (noxHazard) {
      Serial.println(" - NOX");

      anyHazard = true;
    }

    if (co2Hazard) {
      Serial.println(" - CO2");

      anyHazard = true;
    }

    if (!anyHazard) {
      Serial.println(" - NONE");
    }
  }

  Serial.println("-----------------------------");
}
