from arduino.app_utils import App, Bridge
from arduino.app_bricks.video_objectdetection import VideoObjectDetection

import csv
import os
import time
import threading
from datetime import datetime


# ==================================================
# SETTINGS
# ==================================================

DETECTION_THRESHOLD = 0.10
PPE_TIMEOUT_SECONDS = 3.0

# Must match sketch.ino
PM25_TRIGGER = 100.0
PM10_TRIGGER = 150.0
VOC_TRIGGER = 200
NOX_TRIGGER = 100
CO2_TRIGGER = 5000


# ==================================================
# ALARM MODES
#
# Must match sketch.ino
# ==================================================

ALARM_OFF = 0
ALARM_PM = 1
ALARM_VOC = 2
ALARM_MULTIPLE = 3


# ==================================================
# CSV LOGGING
# ==================================================

# /app is bind-mounted to:
#
# /home/arduino/ArduinoApps/pppe_detector
#
# so /app/data survives container recreation.
LOG_DIRECTORY = "/app/data"

os.makedirs(
    LOG_DIRECTORY,
    exist_ok=True
)

log_lock = threading.Lock()


# ==================================================
# PPE DETECTOR
# ==================================================

detector = VideoObjectDetection(
    confidence=DETECTION_THRESHOLD,
    debounce_sec=0.0
)


# ==================================================
# SYSTEM STATE
# ==================================================

yolo_active = False

active_hazards = []

# Remember the alarm mode currently requested.
# This prevents us from repeatedly sending the
# exact same Bridge command.
current_alarm_mode = ALARM_OFF


# Latest PPE states
current_mask_worn = None
current_goggles_worn = None
current_respirator_worn = None
current_ppe_confirmed = None


# Watchdog state
last_required_ppe_time = 0.0
last_ppe_confirmed = None


# ==================================================
# DAILY CSV FILE
# ==================================================

def get_log_path():

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    return os.path.join(
        LOG_DIRECTORY,
        f"sen66_{today}.csv"
    )


# ==================================================
# GET BEST YOLO CONFIDENCE
# ==================================================

def best_confidence(
    results,
    label
):

    detections = results.get(
        label,
        []
    )

    if not detections:
        return 0.0

    return max(
        float(
            detection.get(
                "confidence",
                0.0
            )
        )
        for detection in detections
    )


# ==================================================
# REQUIRED PPE
# ==================================================

def get_required_ppe_text():

    has_particulate = (
        "PARTICULATE" in active_hazards
    )

    has_voc = (
        "VOC" in active_hazards
    )


    # VOC always requires the respirator.
    #
    # This also covers:
    # PARTICULATE + VOC
    if has_voc:
        return "RESPIRATOR"


    # Particulate by itself accepts either
    # the mask or respirator.
    if has_particulate:
        return "MASK OR RESPIRATOR"


    # NOX / CO2 currently do not use the
    # mask/respirator PPE alarm logic.
    return "NONE"


def required_ppe_confirmed(
    mask_worn,
    respirator_worn
):

    required = get_required_ppe_text()


    if required == "RESPIRATOR":

        return respirator_worn


    if required == "MASK OR RESPIRATOR":

        return (
            mask_worn
            or
            respirator_worn
        )


    return None


# ==================================================
# DETERMINE ALARM TYPE
# ==================================================

def get_required_alarm_mode():

    has_particulate = (
        "PARTICULATE" in active_hazards
    )

    has_voc = (
        "VOC" in active_hazards
    )


    # PM + VOC gets the high-priority
    # multiple-hazard pattern.
    if has_particulate and has_voc:

        return ALARM_MULTIPLE


    # VOC-only alarm
    if has_voc:

        return ALARM_VOC


    # PM-only alarm
    if has_particulate:

        return ALARM_PM


    # No PM/VOC respiratory alarm
    return ALARM_OFF


# ==================================================
# SEND ALARM COMMAND TO STM32
# ==================================================

def set_alarm_state(
    turn_on,
    force=False
):

    global current_alarm_mode


    if turn_on:

        requested_mode = (
            get_required_alarm_mode()
        )

    else:

        requested_mode = ALARM_OFF


    # Do not repeatedly send the exact
    # same command unless explicitly forced.
    if (
        requested_mode == current_alarm_mode
        and not force
    ):

        return


    try:

        Bridge.call(
            "set_alarm",
            requested_mode
        )


        current_alarm_mode = (
            requested_mode
        )


        if requested_mode == ALARM_OFF:

            print(
                ">>> ALARM OFF <<<"
            )


        elif requested_mode == ALARM_PM:

            print(
                ">>> PARTICULATE ALARM ACTIVE <<<"
            )


        elif requested_mode == ALARM_VOC:

            print(
                ">>> VOC ALARM ACTIVE <<<"
            )


        elif requested_mode == ALARM_MULTIPLE:

            print(
                ">>> MULTIPLE HAZARD ALARM ACTIVE <<<"
            )


    except Exception as e:

        print(
            f"ALARM BRIDGE ERROR: {e}"
        )


# ==================================================
# PPE DETECTION CALLBACK
# ==================================================

def on_detections(results):

    global yolo_active

    global current_mask_worn
    global current_goggles_worn
    global current_respirator_worn
    global current_ppe_confirmed

    global last_required_ppe_time
    global last_ppe_confirmed


    if not yolo_active:
        return


    print()
    print(
        "--------------------------------"
    )

    print(
        "--- PPE RESULT ---"
    )

    print(results)


    # ==================================================
    # MODEL SCORES
    # ==================================================

    mask_score = best_confidence(
        results,
        "mask"
    )


    goggles_score = best_confidence(
        results,
        "goggles"
    )


    respirator_score = best_confidence(
        results,
        "respirator"
    )


    # ==================================================
    # DETECTION STATES
    # ==================================================

    mask_worn = (
        mask_score > 0.0
    )


    goggles_worn = (
        goggles_score > 0.0
    )


    respirator_worn = (
        respirator_score > 0.0
    )


    # ==================================================
    # CHECK PPE AGAINST HAZARD
    # ==================================================

    ppe_confirmed = (
        required_ppe_confirmed(
            mask_worn,
            respirator_worn
        )
    )


    # ==================================================
    # SAVE LATEST PPE STATE FOR CSV
    # ==================================================

    current_mask_worn = (
        mask_worn
    )

    current_goggles_worn = (
        goggles_worn
    )

    current_respirator_worn = (
        respirator_worn
    )

    current_ppe_confirmed = (
        ppe_confirmed
    )


    # ==================================================
    # PRINT RESULTS
    # ==================================================

    print(
        f"Mask={mask_score:.2f}  "
        f"Goggles={goggles_score:.2f}  "
        f"Respirator={respirator_score:.2f}"
    )


    print(
        f"MASK CONFIRMED: "
        f"{mask_worn}"
    )


    print(
        f"SAFETY GOGGLES CONFIRMED: "
        f"{goggles_worn}"
    )


    print(
        f"RESPIRATOR CONFIRMED: "
        f"{respirator_worn}"
    )


    print()

    print(
        "ACTIVE HAZARDS:"
    )


    if active_hazards:

        for hazard in active_hazards:

            print(
                f" - {hazard}"
            )

    else:

        print(
            " - NONE"
        )


    required_ppe = (
        get_required_ppe_text()
    )


    print()

    print(
        f"REQUIRED PPE: "
        f"{required_ppe}"
    )


    print(
        f"REQUIRED PPE CONFIRMED: "
        f"{ppe_confirmed}"
    )


    # ==================================================
    # ALARM / PPE LOGIC
    # ==================================================

    if ppe_confirmed is True:

        # Required PPE is present.
        #
        # Refresh watchdog because we have
        # just positively confirmed PPE.
        last_required_ppe_time = (
            time.monotonic()
        )


        if last_ppe_confirmed is not True:

            print()

            print(
                "CORRECT PPE DETECTED"
            )

            print(
                "-> ALARM OFF"
            )


        set_alarm_state(
            False
        )


        last_ppe_confirmed = (
            True
        )


    elif ppe_confirmed is False:

        # Required PPE is missing.
        #
        # Select PM / VOC / multiple alarm
        # depending on active hazards.

        if last_ppe_confirmed is not False:

            print()

            print(
                "REQUIRED PPE NOT DETECTED"
            )


            if (
                get_required_alarm_mode()
                == ALARM_PM
            ):

                print(
                    "-> PM ALARM"
                )


            elif (
                get_required_alarm_mode()
                == ALARM_VOC
            ):

                print(
                    "-> VOC ALARM"
                )


            elif (
                get_required_alarm_mode()
                == ALARM_MULTIPLE
            ):

                print(
                    "-> MULTIPLE HAZARD ALARM"
                )


        set_alarm_state(
            True
        )


        last_ppe_confirmed = (
            False
        )


    else:

        # NOX / CO2 currently do not have
        # mask or respirator requirements.
        set_alarm_state(
            False
        )


    print(
        "--------------------------------"
    )


# ==================================================
# START YOLO
# ==================================================

def start_yolo(reason):

    global yolo_active
    global active_hazards

    global current_mask_worn
    global current_goggles_worn
    global current_respirator_worn
    global current_ppe_confirmed

    global last_required_ppe_time
    global last_ppe_confirmed

    global current_alarm_mode


    if yolo_active:

        print(
            "PPE detection is already active."
        )

        return


    # ==================================================
    # RECEIVE MULTIPLE HAZARDS
    #
    # Example:
    #
    # PARTICULATE,VOC
    #
    # becomes:
    #
    # ["PARTICULATE", "VOC"]
    # ==================================================

    active_hazards = [

        hazard.strip()

        for hazard
        in str(reason).split(",")

        if hazard.strip()
    ]


    # ==================================================
    # RESET PPE STATE
    # ==================================================

    current_mask_worn = None

    current_goggles_worn = None

    current_respirator_worn = None

    current_ppe_confirmed = None


    last_ppe_confirmed = None


    last_required_ppe_time = (
        time.monotonic()
    )


    # Alarm stays OFF until YOLO/watchdog
    # determines required PPE is missing.
    current_alarm_mode = ALARM_OFF


    yolo_active = True


    # ==================================================
    # PRINT EVENT
    # ==================================================

    print()

    print(
        "================================"
    )

    print(
        "SEN66 TRIGGER RECEIVED"
    )

    print(
        "================================"
    )


    print(
        "ACTIVE HAZARDS:"
    )


    if active_hazards:

        for hazard in active_hazards:

            print(
                f" - {hazard}"
            )

    else:

        print(
            " - UNKNOWN"
        )


    print()

    print(
        f"REQUIRED PPE: "
        f"{get_required_ppe_text()}"
    )


    alarm_mode = (
        get_required_alarm_mode()
    )


    if alarm_mode == ALARM_PM:

        print(
            "ALARM TYPE: PARTICULATE"
        )


    elif alarm_mode == ALARM_VOC:

        print(
            "ALARM TYPE: VOC"
        )


    elif alarm_mode == ALARM_MULTIPLE:

        print(
            "ALARM TYPE: MULTIPLE HAZARDS"
        )


    else:

        print(
            "ALARM TYPE: NONE"
        )


    print()

    print(
        "PPE detection ACTIVE."
    )

    print(
        "Waiting for correct PPE..."
    )

    print(
        "================================"
    )


# ==================================================
# STOP YOLO
# ==================================================

def stop_yolo(command):

    global yolo_active
    global active_hazards

    global current_mask_worn
    global current_goggles_worn
    global current_respirator_worn
    global current_ppe_confirmed

    global last_required_ppe_time
    global last_ppe_confirmed

    global current_alarm_mode


    # ==================================================
    # TURN ALARM OFF FIRST
    # ==================================================

    set_alarm_state(
        False,
        force=True
    )


    # ==================================================
    # RESET SYSTEM STATE
    # ==================================================

    yolo_active = False

    active_hazards = []


    current_mask_worn = None

    current_goggles_worn = None

    current_respirator_worn = None

    current_ppe_confirmed = None


    last_required_ppe_time = 0.0

    last_ppe_confirmed = None

    current_alarm_mode = ALARM_OFF


    print()

    print(
        "================================"
    )

    print(
        "SEN66 LEVELS RETURNED TO SAFE"
    )

    print(
        "PPE detection DISABLED."
    )

    print(
        "ALARM OFF."
    )

    print(
        "Waiting for next SEN66 trigger..."
    )

    print(
        "================================"
    )


# ==================================================
# SEN66 CSV LOGGER
# ==================================================

def log_sen66_reading(data):

    values = str(data).split(",")


    if len(values) != 9:

        print(
            f"BAD SEN66 LOG MESSAGE: {data}"
        )

        return


    try:

        pm1 = float(
            values[0]
        )

        pm25 = float(
            values[1]
        )

        pm4 = float(
            values[2]
        )

        pm10 = float(
            values[3]
        )


        humidity = float(
            values[4]
        )

        temperature = float(
            values[5]
        )


        voc = int(
            values[6]
        )

        nox = int(
            values[7]
        )

        co2 = int(
            values[8]
        )


    except ValueError:

        print(
            f"INVALID SEN66 VALUES: {data}"
        )

        return


    # ==================================================
    # HAZARD STATE FROM THIS SENSOR SAMPLE
    # ==================================================

    particulate_hazard = (

        pm25 >= PM25_TRIGGER

        or

        pm10 >= PM10_TRIGGER
    )


    voc_hazard = (

        voc >= VOC_TRIGGER
    )


    nox_hazard = (

        nox >= NOX_TRIGGER
    )


    co2_hazard = (

        co2 >= CO2_TRIGGER
    )


    any_hazard = (

        particulate_hazard

        or

        voc_hazard

        or

        nox_hazard

        or

        co2_hazard
    )


    # ==================================================
    # TIMESTAMP
    # ==================================================

    timestamp = (
        datetime.now().isoformat(
            timespec="seconds"
        )
    )


    log_path = (
        get_log_path()
    )


    file_exists = (
        os.path.exists(
            log_path
        )
    )


    # ==================================================
    # PPE VALUES
    #
    # PPE data is only meaningful while:
    #
    # 1. a hazard exists
    # 2. YOLO is actively checking PPE
    # ==================================================

    if (
        any_hazard
        and
        yolo_active
    ):

        mask_value = (
            current_mask_worn
        )


        respirator_value = (
            current_respirator_worn
        )


        goggles_value = (
            current_goggles_worn
        )


        required_ppe = (
            get_required_ppe_text()
        )


        ppe_value = (
            current_ppe_confirmed
        )


    else:

        mask_value = ""

        respirator_value = ""

        goggles_value = ""

        required_ppe = ""

        ppe_value = ""


    # ==================================================
    # WRITE CSV
    # ==================================================

    with log_lock:

        with open(
            log_path,
            "a",
            newline=""
        ) as file:

            writer = csv.writer(
                file
            )


            # ==================================================
            # CREATE HEADER ON NEW FILE
            # ==================================================

            if not file_exists:

                writer.writerow([

                    "timestamp",

                    "pm1_ug_m3",
                    "pm25_ug_m3",
                    "pm4_ug_m3",
                    "pm10_ug_m3",

                    "humidity_percent",
                    "temperature_c",

                    "voc_index",
                    "nox_index",
                    "co2_ppm",

                    "particulate_hazard",
                    "voc_hazard",
                    "nox_hazard",
                    "co2_hazard",

                    "yolo_active",

                    "mask_detected",
                    "respirator_detected",
                    "goggles_detected",

                    "required_ppe",
                    "ppe_confirmed"
                ])


            # ==================================================
            # WRITE SENSOR ROW
            # ==================================================

            writer.writerow([

                timestamp,

                pm1,
                pm25,
                pm4,
                pm10,

                humidity,
                temperature,

                voc,
                nox,
                co2,

                particulate_hazard,
                voc_hazard,
                nox_hazard,
                co2_hazard,

                yolo_active,

                mask_value,
                respirator_value,
                goggles_value,

                required_ppe,
                ppe_value
            ])


# ==================================================
# PPE WATCHDOG
#
# Handles the behavior we observed where the
# detector may stop producing callbacks when
# nothing is detected.
# ==================================================

def ppe_watchdog():

    global last_required_ppe_time

    global last_ppe_confirmed

    global current_ppe_confirmed


    while True:

        time.sleep(
            0.25
        )


        if not yolo_active:

            continue


        required = (
            get_required_ppe_text()
        )


        # NOX / CO2 do not currently use
        # respiratory PPE buzzer logic.
        if required == "NONE":

            continue


        now = (
            time.monotonic()
        )


        if (
            now - last_required_ppe_time
            >= PPE_TIMEOUT_SECONDS
        ):

            if last_ppe_confirmed is not False:

                print()

                print(
                    "================================"
                )

                print(
                    "PPE WATCHDOG TIMEOUT"
                )


                print(
                    f"Required PPE not confirmed: "
                    f"{required}"
                )


                print(
                    "Activating hazard-specific alarm."
                )


                print(
                    "================================"
                )


                set_alarm_state(
                    True
                )


                last_ppe_confirmed = (
                    False
                )


                current_ppe_confirmed = (
                    False
                )


# ==================================================
# REGISTER YOLO CALLBACK
# ==================================================

detector.on_detect_all(
    on_detections
)


# ==================================================
# REGISTER STM32 -> LINUX COMMANDS
# ==================================================

Bridge.provide(
    "start_yolo",
    start_yolo
)


Bridge.provide(
    "stop_yolo",
    stop_yolo
)


Bridge.provide(
    "sen66_reading",
    log_sen66_reading
)


# ==================================================
# START WATCHDOG
# ==================================================

watchdog_thread = threading.Thread(
    target=ppe_watchdog,
    daemon=True
)

watchdog_thread.start()


# ==================================================
# STARTUP
# ==================================================

print()

print(
    "================================"
)

print(
    "Linux PPE + SEN66 Logger started."
)

print(
    "================================"
)


print()

print(
    "PPE RULES:"
)


print(
    "PARTICULATE -> MASK OR RESPIRATOR"
)


print(
    "VOC         -> RESPIRATOR"
)


print(
    "PM + VOC    -> RESPIRATOR"
)


print()

print(
    "ALARM MODES:"
)


print(
    "PM         -> PM alarm sound"
)


print(
    "VOC        -> VOC alarm sound"
)


print(
    "PM + VOC   -> Multiple hazard sound"
)


print()

print(
    "CSV logs saved to:"
)

print(
    LOG_DIRECTORY
)


print()

print(
    "Waiting for SEN66 data..."
)


print(
    "PPE detection currently OFF."
)


print(
    "Alarm OFF."
)


print(
    "================================"
)


# ==================================================
# RUN
# ==================================================

App.run()