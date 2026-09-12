# Personal Protective Equipment Detector

This Arduino UNO Q application monitors a Sensirion SEN66, logs readings to
daily CSV files, and uses a local image-classification model to check for a
mask when particulate matter or VOC levels exceed the configured limits.

## Exposure dashboard

The read-only dashboard displays the existing CSV logs without changing them.
It provides daily graphs, configured hazard lines, daily maximum and average
values, exceedance-event counts, and estimated time above each threshold.

The dashboard is part of the same Arduino App as the PPE detector. It starts on
the Linux side when the application starts and listens on port `7000` on the
local network. It does not require an internet connection after the App has
been provisioned.

### Start the application

In Arduino App Lab, select **PPE Detection with image classification** and
press **Run**. From an SSH terminal on the UNO Q, the equivalent command is:

```bash
arduino-app-cli app start \
  ~/ArduinoApps/ppe-detection-with-image-classification
```

Only one Arduino App can run at a time. Starting this App stops any other App
that is currently running.

The project remains configured as the default App for normal boot startup. The
USB camera must already be connected and visible to Linux when App Lab starts
the default App. Check the default setting with:

```bash
arduino-app-cli properties get user:ppe-detection-with-image-classification
```

### Open the dashboard from a phone

1. Connect the phone and UNO Q to the same local Wi-Fi network.
2. Find the UNO Q's IPv4 address:

   ```bash
   hostname -I
   ```

3. Open `http://UNO-Q-IP:7000` in Safari or another browser. For example:

   ```text
   http://192.168.1.42:7000
   ```

The exact dashboard URL is also printed in the App log during startup:

```bash
arduino-app-cli app logs \
  ~/ArduinoApps/ppe-detection-with-image-classification \
  --tail 80
```

The server binds only to the UNO Q's local network interfaces. This prototype
does not configure router port forwarding, tunnelling, or public-internet
access.

### Refreshing data

Select any date found in the CSV logs and press **Refresh readings** to include
new files or rows. The requested day is filtered on the UNO Q. Graph data is
limited to 1,200 plotted samples to keep the page responsive, while summary
statistics use all valid samples for the selected day.

**Focus on activity** is enabled by default for pollutant graphs. It trims long
quiet portions from the visible time range so changes and exposure events are
easier to inspect. Turn it off to return every chart to the full-day timeline.

Graph lines are green below the configured limit and red above it. For PM and
VOC, an above-limit section returns to green when the existing CSV data says
PPE was confirmed. CO₂ and NOx remain red above their limits because the App's
existing safety rules require ventilation or leaving the area rather than PPE.

The CSV files remain the source of truth and are stored in:

```text
/home/arduino/ArduinoApps/ppe-detection-with-image-classification/data
```

Inside the App container, that same directory is available as `/app/data`.
Files are named `sen66_YYYY-MM-DD.csv`, and timestamps use ISO 8601 local time
to the nearest second, such as `2026-09-10T17:23:42`.

### Troubleshooting

Check App status and logs:

```bash
arduino-app-cli app list
arduino-app-cli app logs \
  ~/ArduinoApps/ppe-detection-with-image-classification \
  --tail 100
```

Confirm that the dashboard port is listening:

```bash
ss -ltn | grep ':7000'
```

The dashboard deliberately ignores malformed rows, incomplete writes, missing
values, NUL padding, and known SEN66 error sentinel values. It never edits or
deletes a CSV file.


