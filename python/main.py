import csv
import os
import threading
import time
from datetime import datetime

from arduino.app_bricks.video_imageclassification import VideoImageClassification
from arduino.app_peripherals.camera import Camera
from arduino.app_utils import App, Bridge

# Detection settings
DETECTION_THRESHOLD = 0.10
PPE_TIMEOUT_SECONDS = 3.0

# Keep these hazard limits in sync with sketch.ino.
PM1_TRIGGER = 20.0
PM25_TRIGGER = 40.0
PM4_TRIGGER = 70.0
PM10_TRIGGER = 100.0
VOC_TRIGGER = 2000
NOX_TRIGGER = 100
CO2_TRIGGER = 3000


# Need to be the same as the values in sketch.ino.
ALARM_OFF = 0
ALARM_PM1 = 1
ALARM_PM25 = 2
ALARM_PM4 = 3
ALARM_PM10 = 4
ALARM_VOC = 5
ALARM_MULTIPLE = 6
ALARM_CO2 = 7
ALARM_NOX = 8

PARTICULATE_HAZARDS = ("PM1", "PM2.5", "PM4", "PM10")


# The app directory is mounted at /app inside the container. Saving logs here
# keeps them in the project when the container is recreated.
LOG_DIRECTORY = "/app/data"

os.makedirs(LOG_DIRECTORY, exist_ok=True)

log_lock = threading.Lock()
alarm_lock = threading.Lock()


# Use the first USB camera explicitly instead of automatic camera selection.
camera = Camera("usb:0", resolution=(640, 480), fps=10)


# PPE image classifier
classifier = VideoImageClassification(
    camera=camera, confidence=DETECTION_THRESHOLD, debounce_sec=0.0
)


# State shared by the Bridge handlers, detector callback, and watchdog
yolo_active = False

active_hazards = []

# Remember the last request so we do not send the same Bridge command repeatedly.
current_alarm_mode = ALARM_OFF


# Most recent classifier results
current_mask_worn = None
current_ppe_confirmed = None


# PPE watchdog state
last_required_ppe_time = 0.0
last_ppe_confirmed = None


# Use a separate log file for each day.
def get_log_path():

    today = datetime.now().strftime("%Y-%m-%d")

    return os.path.join(LOG_DIRECTORY, f"sen66_{today}.csv")


# Describe the respiratory PPE required by the current hazards.
def get_required_ppe_text():

    has_particulate = any(hazard in active_hazards for hazard in PARTICULATE_HAZARDS)

    has_voc = "VOC" in active_hazards

    # This classifier can distinguish a mask from no mask.
    if has_particulate or has_voc:
        return "MASK"

    # NOX and CO2 do not currently trigger the respiratory PPE alarm.
    return "NONE"


def required_ppe_confirmed(mask_worn):

    required = get_required_ppe_text()

    if required == "MASK":

        return mask_worn

    return None


# Choose the buzzer pattern for the current combination of hazards.
def get_required_alarm_mode():

    has_particulate = any(hazard in active_hazards for hazard in PARTICULATE_HAZARDS)

    has_voc = "VOC" in active_hazards

    # PM and VOC together use the higher-priority multiple-hazard pattern.
    if has_particulate and has_voc:

        return ALARM_MULTIPLE

    if has_voc:

        return ALARM_VOC

    # Prefer the smallest active particle size when several limits are high.
    if "PM1" in active_hazards:
        return ALARM_PM1

    if "PM2.5" in active_hazards:
        return ALARM_PM25

    if "PM4" in active_hazards:
        return ALARM_PM4

    if "PM10" in active_hazards:
        return ALARM_PM10

    return ALARM_OFF


# Tell the sketch which complete alarm pattern to use.
def set_alarm_state(turn_on, force=False):

    global current_alarm_mode

    # Serialize commands from the classifier, watchdog, and Bridge handlers.
    with alarm_lock:

        # A safe-state notification may arrive while an older classification is
        # still being processed. Never allow that stale result to restart the alarm.
        if turn_on and not yolo_active:
            return

        if turn_on:

            requested_mode = get_required_alarm_mode()

        else:

            requested_mode = ALARM_OFF

        # Avoid unnecessary Bridge traffic unless the caller requests a resend.
        if requested_mode == current_alarm_mode and not force:

            return

        try:

            # No return value is needed. Using notify also avoids blocking inside
            # a Bridge handler while waiting for the sketch to answer.
            Bridge.notify("set_alarm", requested_mode)

            current_alarm_mode = requested_mode

            if requested_mode == ALARM_OFF:

                print(">>> ALARM OFF <<<")

            elif requested_mode == ALARM_PM1:
                print(">>> PM1 ALARM ACTIVE <<<")

            elif requested_mode == ALARM_PM25:
                print(">>> PM2.5 ALARM ACTIVE <<<")

            elif requested_mode == ALARM_PM4:
                print(">>> PM4 ALARM ACTIVE <<<")

            elif requested_mode == ALARM_PM10:
                print(">>> PM10 ALARM ACTIVE <<<")

            elif requested_mode == ALARM_VOC:

                print(">>> VOC ALARM ACTIVE <<<")

            elif requested_mode == ALARM_MULTIPLE:

                print(">>> MULTIPLE HAZARD ALARM ACTIVE <<<")

        except Exception as e:

            print(f"ALARM BRIDGE ERROR: {e}")


# Process each set of results produced by the PPE classifier.
def on_classifications(results):

    global yolo_active

    global current_mask_worn
    global current_ppe_confirmed

    global last_required_ppe_time
    global last_ppe_confirmed

    if not yolo_active:
        return

    print()
    print("--------------------------------")

    print("--- PPE CLASSIFICATION ---")

    print(results)

    mask_score = float(results.get("mask", 0.0))

    none_score = float(results.get("none", 0.0))

    # Treat the highest-scoring class as the result for this frame.
    mask_worn = mask_score > none_score and mask_score > 0.0

    # Check the detected equipment against the current hazard requirements.
    ppe_confirmed = required_ppe_confirmed(mask_worn)

    # Save the result so the sensor logger can include it in the next CSV row.
    current_mask_worn = mask_worn

    current_ppe_confirmed = ppe_confirmed

    # Print a readable summary to the application log.
    print(f"Mask={mask_score:.2f}  None={none_score:.2f}")

    print(f"MASK CONFIRMED: " f"{mask_worn}")

    print()

    print("ACTIVE HAZARDS:")

    if active_hazards:

        for hazard in active_hazards:

            print(f" - {hazard}")

    else:

        print(" - NONE")

    required_ppe = get_required_ppe_text()

    print()

    print(f"REQUIRED PPE: " f"{required_ppe}")

    print(f"REQUIRED PPE CONFIRMED: " f"{ppe_confirmed}")

    # Silence the alarm when the required PPE is present; otherwise select the
    # pattern that matches the active hazards.
    if ppe_confirmed is True:

        # A positive detection resets the watchdog timer.
        last_required_ppe_time = time.monotonic()

        if last_ppe_confirmed is not True:

            print()

            print("CORRECT PPE DETECTED")

            print("-> ALARM OFF")

        set_alarm_state(False)

        last_ppe_confirmed = True

    elif ppe_confirmed is False:

        if last_ppe_confirmed is not False:

            print()

            print("REQUIRED PPE NOT DETECTED")

            alarm_mode = get_required_alarm_mode()

            if alarm_mode == ALARM_PM1:
                print("-> PM1 ALARM")

            elif alarm_mode == ALARM_PM25:
                print("-> PM2.5 ALARM")

            elif alarm_mode == ALARM_PM4:
                print("-> PM4 ALARM")

            elif alarm_mode == ALARM_PM10:
                print("-> PM10 ALARM")

            elif alarm_mode == ALARM_VOC:

                print("-> VOC ALARM")

            elif alarm_mode == ALARM_MULTIPLE:

                print("-> MULTIPLE HAZARD ALARM")

        set_alarm_state(True)

        last_ppe_confirmed = False

    else:

        # NOX and CO2 do not currently use the camera-based PPE alarm.
        set_alarm_state(False)

    print("--------------------------------")


# Start checking PPE when the sketch reports a hazard.
def start_yolo(reason):

    global yolo_active
    global active_hazards

    global current_mask_worn
    global current_ppe_confirmed

    global last_required_ppe_time
    global last_ppe_confirmed

    global current_alarm_mode

    if yolo_active:

        print("PPE detection is already active.")

        return

    # The sketch sends PPE hazards as text such as "PM2.5,PM10,VOC".
    active_hazards = [
        hazard.strip() for hazard in str(reason).split(",") if hazard.strip()
    ]

    # Clear results left over from the previous hazard event.
    current_mask_worn = None

    current_ppe_confirmed = None

    last_ppe_confirmed = None

    last_required_ppe_time = time.monotonic()

    # Stay silent until the detector or watchdog confirms that PPE is missing.
    with alarm_lock:
        current_alarm_mode = ALARM_OFF

    yolo_active = True

    # Record the new hazard event in the application log.
    print()

    print("================================")

    print("SEN66 TRIGGER RECEIVED")

    print("================================")

    print("ACTIVE HAZARDS:")

    if active_hazards:

        for hazard in active_hazards:

            print(f" - {hazard}")

    else:

        print(" - UNKNOWN")

    print()

    print(f"REQUIRED PPE: " f"{get_required_ppe_text()}")

    alarm_mode = get_required_alarm_mode()

    if alarm_mode == ALARM_PM1:
        print("ALARM TYPE: PM1")

    elif alarm_mode == ALARM_PM25:
        print("ALARM TYPE: PM2.5")

    elif alarm_mode == ALARM_PM4:
        print("ALARM TYPE: PM4")

    elif alarm_mode == ALARM_PM10:
        print("ALARM TYPE: PM10")

    elif alarm_mode == ALARM_VOC:

        print("ALARM TYPE: VOC")

    elif alarm_mode == ALARM_MULTIPLE:

        print("ALARM TYPE: MULTIPLE HAZARDS")

    else:

        print("ALARM TYPE: NONE")

    print()

    print("PPE detection ACTIVE.")

    print("Waiting for correct PPE...")

    print("================================")


# Stop checking PPE when the sketch reports that the air is safe again.
def stop_yolo(command):

    global yolo_active
    global active_hazards

    global current_mask_worn
    global current_ppe_confirmed

    global last_required_ppe_time
    global last_ppe_confirmed

    # Disable PPE decisions first so an older classification or watchdog check
    # cannot send an alarm-on command after the final alarm-off command.
    yolo_active = False

    active_hazards = []

    # Send the off command even if our saved state already says the alarm is off.
    set_alarm_state(False, force=True)

    # Clear all remaining state associated with the finished hazard event.

    current_mask_worn = None

    current_ppe_confirmed = None

    last_required_ppe_time = 0.0

    last_ppe_confirmed = None

    print()

    print("================================")

    print("SEN66 LEVELS RETURNED TO SAFE")

    print("PPE detection DISABLED.")

    print("ALARM OFF.")

    print("Waiting for next SEN66 trigger...")

    print("================================")


# Parse one SEN66 message from the sketch and append it to today's CSV file.
def log_sen66_reading(data):

    values = str(data).split(",")

    if len(values) != 9:

        print(f"BAD SEN66 LOG MESSAGE: {data}")

        return

    try:

        pm1 = float(values[0])

        pm25 = float(values[1])

        pm4 = float(values[2])

        pm10 = float(values[3])

        humidity = float(values[4])

        temperature = float(values[5])

        voc = int(values[6])

        nox = int(values[7])

        co2 = int(values[8])

    except ValueError:

        print(f"INVALID SEN66 VALUES: {data}")

        return

    # Calculate hazard flags from this sample rather than from saved state.
    particulate_hazard = (
        pm1 >= PM1_TRIGGER
        or pm25 >= PM25_TRIGGER
        or pm4 >= PM4_TRIGGER
        or pm10 >= PM10_TRIGGER
    )

    voc_hazard = voc >= VOC_TRIGGER

    nox_hazard = nox >= NOX_TRIGGER

    co2_hazard = co2 >= CO2_TRIGGER

    any_hazard = particulate_hazard or voc_hazard or nox_hazard or co2_hazard

    timestamp = datetime.now().isoformat(timespec="seconds")

    log_path = get_log_path()

    file_exists = os.path.exists(log_path)

    # PPE results only have meaning while a hazard is active and the detector
    # is running. Leave those CSV fields blank at all other times.
    if any_hazard and yolo_active:

        mask_value = current_mask_worn

        required_ppe = get_required_ppe_text()

        ppe_value = current_ppe_confirmed

    else:

        mask_value = ""

        required_ppe = ""

        ppe_value = ""

    # The lock prevents detector and Bridge threads from writing together.
    with log_lock:

        with open(log_path, "a", newline="") as file:

            writer = csv.writer(file)

            # A new daily file needs its column headings before the first row.
            if not file_exists:

                writer.writerow(
                    [
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
                        "ppe_confirmed",
                    ]
                )

            writer.writerow(
                [
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
                    "",
                    "",
                    required_ppe,
                    ppe_value,
                ]
            )


# The classifier may stop sending results if no class passes the threshold. This
# watchdog treats a long gap in positive mask classifications as missing PPE.
def ppe_watchdog():

    global last_required_ppe_time

    global last_ppe_confirmed

    global current_ppe_confirmed

    while True:

        time.sleep(0.25)

        if not yolo_active:

            continue

        required = get_required_ppe_text()

        # NOX and CO2 do not currently use the camera-based PPE alarm.
        if required == "NONE":

            continue

        now = time.monotonic()

        if now - last_required_ppe_time >= PPE_TIMEOUT_SECONDS:

            if last_ppe_confirmed is not False:

                print()

                print("================================")

                print("PPE WATCHDOG TIMEOUT")

                print(f"Required PPE not confirmed: " f"{required}")

                print("Activating hazard-specific alarm.")

                print("================================")

                set_alarm_state(True)

                last_ppe_confirmed = False

                current_ppe_confirmed = False


# Send model results to on_classifications.
classifier.on_detect_all(on_classifications)


# Receive hazard state and sensor readings from the sketch.
Bridge.provide("start_yolo", start_yolo)


Bridge.provide("stop_yolo", stop_yolo)


Bridge.provide("sen66_reading", log_sen66_reading)


# Run the watchdog alongside the application's Bridge event loop.
watchdog_thread = threading.Thread(target=ppe_watchdog, daemon=True)

watchdog_thread.start()


# Print a quick configuration summary at startup.
print()

print("================================")

print("Linux PPE + SEN66 Logger started.")

print("================================")


print()

print("PPE RULES:")


print("PARTICULATE -> MASK")


print("VOC         -> MASK")


print("PM + VOC    -> MASK")


print()

print("ALARM MODES:")


print("PM1        -> very fast repeating beep")

print("PM2.5      -> fast repeating beep")

print("PM4        -> medium repeating beep")

print("PM10       -> slow repeating beep")

print("VOC        -> VOC alarm sound")

print("PM + VOC   -> Multiple hazard sound")

print("CO2        -> Increase airflow (no camera)")

print("NOX        -> Leave the area (no camera)")


print()

print("CSV logs saved to:")

print(LOG_DIRECTORY)


print()

print("Waiting for SEN66 data...")


print("PPE detection currently OFF.")


print("Alarm OFF.")


print("================================")


# App.run() must remain the final statement in this file.
App.run()
