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


// ==================================================
// BUZZER
// ==================================================

const int BUZZER_PIN = 9;

// Alarm modes
const int ALARM_OFF = 0;
const int ALARM_PM = 1;
const int ALARM_VOC = 2;
const int ALARM_MULTIPLE = 3;

volatile int currentAlarmMode = ALARM_OFF;

unsigned long alarmTimer = 0;
int alarmStep = 0;


// ==================================================
// SEN66 THRESHOLDS
// ==================================================

const float PM25_TRIGGER = 100.0;
const float PM10_TRIGGER = 150.0;

const int VOC_TRIGGER = 200;
const int NOX_TRIGGER = 100;

const uint16_t CO2_TRIGGER = 5000;


// ==================================================
// FILTERING
// ==================================================

const int REQUIRED_DANGER_READINGS = 3;
const int REQUIRED_SAFE_READINGS = 5;

int dangerCount = 0;
int safeCount = 0;

bool yoloTriggered = false;


// ==================================================
// 30 SECOND WARM-UP, sensor values 
// ==================================================

const unsigned long SENSOR_WARMUP_TIME = 30000;

unsigned long sensorStartTime = 0;
unsigned long lastMatrixUpdate = 0;


// ==================================================
// MATRIX FRAMES
// 8 rows x 13 columns
// ==================================================


// READY / CHECKMARK
uint8_t readyFrame[104] = {

    0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,1,0,
    0,0,0,0,0,0,0,0,0,0,1,0,0,
    0,0,0,0,0,0,0,0,0,1,0,0,0,
    0,0,0,0,0,0,0,0,1,0,0,0,0,
    0,0,1,0,0,0,0,1,0,0,0,0,0,
    0,0,0,1,0,0,1,0,0,0,0,0,0,
    0,0,0,0,1,1,0,0,0,0,0,0,0
};


// PM
uint8_t pmFrame[104] = {

    1,1,1,0,0,1,0,0,0,1,0,0,0,
    1,0,1,0,0,1,1,0,1,1,0,0,0,
    1,0,1,0,0,1,0,1,0,1,0,0,0,
    1,1,1,0,0,1,0,1,0,1,0,0,0,
    1,0,0,0,0,1,0,0,0,1,0,0,0,
    1,0,0,0,0,1,0,0,0,1,0,0,0,
    1,0,0,0,0,1,0,0,0,1,0,0,0,
    1,0,0,0,0,1,0,0,0,1,0,0,0
};


// VOC shown as "VO"
uint8_t vocFrame[104] = {

    1,0,0,0,1,0,0,1,1,1,0,0,0,
    1,0,0,0,1,0,1,0,0,0,1,0,0,
    1,0,0,0,1,0,1,0,0,0,1,0,0,
    1,0,0,0,1,0,1,0,0,0,1,0,0,
    1,0,0,0,1,0,1,0,0,0,1,0,0,
    0,1,0,1,0,0,1,0,0,0,1,0,0,
    0,1,0,1,0,0,1,0,0,0,1,0,0,
    0,0,1,0,0,0,0,1,1,1,0,0,0
};


// MULTIPLE HAZARDS / warning triangle
uint8_t multipleFrame[104] = {

    0,0,0,0,0,0,1,0,0,0,0,0,0,
    0,0,0,0,0,1,0,1,0,0,0,0,0,
    0,0,0,0,1,0,0,0,1,0,0,0,0,
    0,0,0,1,0,0,1,0,0,1,0,0,0,
    0,0,1,0,0,0,1,0,0,0,1,0,0,
    0,1,0,0,0,0,1,0,0,0,0,1,0,
    1,0,0,0,0,0,0,0,0,0,0,0,1,
    1,1,1,1,1,1,1,1,1,1,1,1,1
};


// ==================================================
// WARM-UP DISPLAY
// ==================================================

void showWarmupProgress(unsigned long elapsedMs) {

    uint8_t frame[104] = {0};

    int ledsToLight =
        (elapsedMs * 104UL) / SENSOR_WARMUP_TIME;

    if (ledsToLight < 0) {
        ledsToLight = 0;
    }

    if (ledsToLight > 104) {
        ledsToLight = 104;
    }

    for (int i = 0; i < ledsToLight; i++) {
        frame[i] = 1;
    }

    matrix.draw(frame);
}


// ==================================================
// RAW BUZZER
// Active-low
// LOW = ON
// HIGH = OFF
// ==================================================

void buzzerHardware(bool on) {

    digitalWrite(
        BUZZER_PIN,
        on ? LOW : HIGH
    );
}


// ==================================================
// SET ALARM MODE
//
// Called from Linux:
// Bridge.call("set_alarm", 0-3)
// ==================================================

void setAlarm(int alarmMode) {

    currentAlarmMode = alarmMode;

    alarmStep = 0;
    alarmTimer = millis();

    // Start every new mode from silence.
    buzzerHardware(false);


    if (alarmMode == ALARM_OFF) {

        matrix.draw(
            readyFrame
        );
    }

    else if (alarmMode == ALARM_PM) {

        matrix.draw(
            pmFrame
        );
    }

    else if (alarmMode == ALARM_VOC) {

        matrix.draw(
            vocFrame
        );
    }

    else if (alarmMode == ALARM_MULTIPLE) {

        matrix.draw(
            multipleFrame
        );
    }
}


// ==================================================
// NON-BLOCKING ALARM PATTERNS
//
// PM:
// BEEP ----- BEEP -----
//
// VOC:
// BEEP BEEP ----- BEEP BEEP -----
//
// MULTIPLE:
// rapid triple warning
// ==================================================

void updateAlarm() {

    if (currentAlarmMode == ALARM_OFF) {

        buzzerHardware(false);
        return;
    }


    unsigned long now = millis();


    // ==================================================
    // PM
    // 250 ms ON
    // 750 ms OFF
    // ==================================================

    if (currentAlarmMode == ALARM_PM) {

        if (alarmStep == 0) {

            buzzerHardware(true);

            if (now - alarmTimer >= 250) {

                alarmStep = 1;
                alarmTimer = now;
            }
        }

        else {

            buzzerHardware(false);

            if (now - alarmTimer >= 750) {

                alarmStep = 0;
                alarmTimer = now;
            }
        }
    }


    // ==================================================
    // VOC
    // beep-beep, pause
    // ==================================================

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


    // ==================================================
    // MULTIPLE HAZARDS
    // rapid triple beep
    // ==================================================

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
}


// ==================================================
// SETUP
// ==================================================

void setup() {

    Serial.begin(115200);

    while (!Serial) {
        delay(100);
    }


    Bridge.begin();


    // ==================================================
    // BUZZER
    // ==================================================

    digitalWrite(
        BUZZER_PIN,
        HIGH
    );

    pinMode(
        BUZZER_PIN,
        OUTPUT
    );

    buzzerHardware(false);


    // Linux controls alarm TYPE, not raw buzzer state.
    Bridge.provide_safe(
        "set_alarm",
        setAlarm
    );


    // ==================================================
    // MATRIX
    // ==================================================

    matrix.begin();

    matrix.setGrayscaleBits(1);

    matrix.clear();


    // ==================================================
    // SEN66
    // ==================================================

    Wire1.begin();

    sensor.begin(
        Wire1,
        SEN66_I2C_ADDR_6B
    );


    error = sensor.stopMeasurement();


    if (error != NO_ERROR) {

        Serial.println(
            "Stop measurement not successful"
        );
    }


    delay(50);


    error =
        sensor.startContinuousMeasurement();


    if (error != NO_ERROR) {

        Serial.println(
            "Start measurement not successful"
        );
    }


    sensorStartTime = millis();


    Serial.println();
    Serial.println("SEN66 started.");
    Serial.println(
        "30 second warm-up started."
    );
    Serial.println();
}


// ==================================================
// LOOP
// ==================================================

void loop() {

    // IMPORTANT:
    // This keeps the buzzer pattern running without
    // blocking sensor reads or Bridge communication.
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


    // ==================================================
    // WARM-UP
    // ==================================================

    unsigned long elapsedTime =
        millis() - sensorStartTime;


    bool sensorWarmedUp =
        elapsedTime >= SENSOR_WARMUP_TIME;


    if (!sensorWarmedUp) {

        if (
            millis() - lastMatrixUpdate >= 250
        ) {

            lastMatrixUpdate = millis();

            showWarmupProgress(
                elapsedTime
            );
        }
    }


    // ==================================================
    // SEN66 DATA READY
    // ==================================================

    error =
        sensor.getDataReady(
            padding,
            dataReady
        );


    if (error != NO_ERROR) {

        Serial.print(
            "getDataReady error: "
        );

        errorToString(
            error,
            errorMessage,
            sizeof errorMessage
        );

        Serial.println(
            errorMessage
        );

        return;
    }


    if (!dataReady) {
        return;
    }


    // ==================================================
    // READ SEN66
    // ==================================================

    error =
        sensor.readMeasuredValuesAsIntegers(

            massConcentrationPm1p0,
            massConcentrationPm2p5,
            massConcentrationPm4p0,
            massConcentrationPm10p0,

            ambientHumidity,
            ambientTemperature,

            vOCIndex,
            nOxIndex,

            cO2
        );


    if (error != NO_ERROR) {

        Serial.print(
            "SEN66 read error: "
        );

        errorToString(
            error,
            errorMessage,
            sizeof errorMessage
        );

        Serial.println(
            errorMessage
        );

        return;
    }


    // ==================================================
    // CONVERT READINGS
    // ==================================================

    float pm1 =
        massConcentrationPm1p0 / 10.0;

    float pm25 =
        massConcentrationPm2p5 / 10.0;

    float pm4 =
        massConcentrationPm4p0 / 10.0;

    float pm10 =
        massConcentrationPm10p0 / 10.0;

    float humidity =
        ambientHumidity / 100.0;

    float temperature =
        ambientTemperature / 200.0;

    int vocIndex = vOCIndex;
    int noxIndex = nOxIndex;
    uint16_t co2 = cO2;


    // ==================================================
    // SEND EVERY SAMPLE TO LINUX LOGGER
    // ==================================================

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


    Bridge.notify(
        "sen66_reading",
        sensorData
    );


    // ==================================================
    // HAZARDS
    // ==================================================

    bool particulateHazard =

        pm25 >= PM25_TRIGGER ||

        pm10 >= PM10_TRIGGER;


    bool vocHazard =
        vocIndex >= VOC_TRIGGER;


    bool noxHazard =
        noxIndex >= NOX_TRIGGER;


    bool co2Hazard =
        co2 >= CO2_TRIGGER;


    bool dangerCondition =

        particulateHazard ||

        vocHazard ||

        noxHazard ||

        co2Hazard;


    // ==================================================
    // FILTERING
    // ==================================================

    if (!sensorWarmedUp) {

        dangerCount = 0;
        safeCount = 0;

        setAlarm(
            ALARM_OFF
        );

    }

    else {

        if (dangerCondition) {

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


    // ==================================================
    // START YOLO
    // ==================================================

    if (
        sensorWarmedUp &&

        dangerCount >=
        REQUIRED_DANGER_READINGS &&

        !yoloTriggered
    ) {

        String activeHazards = "";


        if (particulateHazard) {

            activeHazards +=
                "PARTICULATE";
        }


        if (vocHazard) {

            if (activeHazards.length() > 0) {
                activeHazards += ",";
            }

            activeHazards +=
                "VOC";
        }


        if (noxHazard) {

            if (activeHazards.length() > 0) {
                activeHazards += ",";
            }

            activeHazards +=
                "NOX";
        }


        if (co2Hazard) {

            if (activeHazards.length() > 0) {
                activeHazards += ",";
            }

            activeHazards +=
                "CO2";
        }


        Bridge.notify(
            "start_yolo",
            activeHazards
        );


        yoloTriggered = true;

        dangerCount = 0;
        safeCount = 0;


        // Display hazard immediately,
        // but do NOT start alarm yet.
        //
        // Python starts the alarm only if
        // required PPE is missing.

        if (
            particulateHazard &&
            vocHazard
        ) {

            matrix.draw(
                multipleFrame
            );
        }

        else if (vocHazard) {

            matrix.draw(
                vocFrame
            );
        }

        else if (particulateHazard) {

            matrix.draw(
                pmFrame
            );
        }

        else {

            matrix.draw(
                multipleFrame
            );
        }


        Serial.println();
        Serial.println(
            "### YOLO TRIGGER SENT ###"
        );

        Serial.print(
            "Active hazards: "
        );

        Serial.println(
            activeHazards
        );
    }


    // ==================================================
    // STOP YOLO
    // ==================================================

    if (
        sensorWarmedUp &&

        safeCount >=
        REQUIRED_SAFE_READINGS &&

        yoloTriggered
    ) {

        Bridge.notify(
            "stop_yolo",
            true
        );


        yoloTriggered = false;

        dangerCount = 0;
        safeCount = 0;


        // Always silence alarm
        setAlarm(
            ALARM_OFF
        );


        matrix.draw(
            readyFrame
        );


        Serial.println();
        Serial.println(
            "### YOLO STOP SENT ###"
        );
    }


    // ==================================================
    // SERIAL
    // ==================================================

    Serial.println(
        "-----------------------------"
    );

    Serial.print("PM2.5: ");
    Serial.println(pm25);

    Serial.print("PM10: ");
    Serial.println(pm10);

    Serial.print("VOC: ");
    Serial.println(vocIndex);

    Serial.print("NOX: ");
    Serial.println(noxIndex);

    Serial.print("CO2: ");
    Serial.println(co2);


    if (sensorWarmedUp) {

        Serial.println(
            "ACTIVE HAZARDS:"
        );

        bool anyHazard = false;


        if (particulateHazard) {

            Serial.println(
                " - PARTICULATE"
            );

            anyHazard = true;
        }


        if (vocHazard) {

            Serial.println(
                " - VOC"
            );

            anyHazard = true;
        }


        if (noxHazard) {

            Serial.println(
                " - NOX"
            );

            anyHazard = true;
        }


        if (co2Hazard) {

            Serial.println(
                " - CO2"
            );

            anyHazard = true;
        }


        if (!anyHazard) {

            Serial.println(
                " - NONE"
            );
        }
    }


    Serial.println(
        "-----------------------------"
    );
}