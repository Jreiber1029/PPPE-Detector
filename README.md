Instructions for adding Edge Impulse Model to App Lab if the normal way does not work, you will need to have nano downloaded in the UNO Q terminal
- After downloading the model and renaming it,you will then need to open your computers PowerShell of terminal not the UNO Qs and run: 
scp "$HOME\Downloads\PPE.eim"arduino@Myboard.local:/home/arduino/PPE.eim
You will be promted to enter your UNO Q password and the model will download.
- Then on the UNO Qs terminal run to create the model directory:
mkdir -p ~/.arduino-bricks/models/custom-ei/ei-model-1036858-1
- Install the model: 
cp ~/PPE.eim \
  ~/.arduino-bricks/models/custom-ei/ei-model-1036858-1/model.eim
- Then run: 
chmod 755 \
  ~/.arduino-bricks/models/custom-ei/ei-model-1036858-1/model.eim
- run: nano ~/.arduino-bricks/models/custom-ei/ei-model-1036858-1/model.yaml
Paste this in

id: ei-model-1036858-1
name: PPE Detector close
runner: brick
description: PPE Detector close
bricks:
  - id: arduino:image_classification
    model_configuration:
      CUSTOM_MODEL_PATH: /home/arduino/.arduino-bricks/models/custom-ei/ei-model-1036858-1
      EI_CLASSIFICATION_MODEL: /home/arduino/.arduino-bricks/models/custom-ei/ei-model-1036858-1/model.eim
  - id: arduino:video_image_classification
    model_configuration:
      CUSTOM_MODEL_PATH: /home/arduino/.arduino-bricks/models/custom-ei/ei-model-1036858-1
      EI_V_CLASSIFICATION_MODEL: /home/arduino/.arduino-bricks/models/custom-ei/ei-model-1036858-1/model.eim
metadata:
  ei-deployment-version: "2"
  ei-engine: tflite
  ei-impulse-id: "1"
  ei-impulse-name: "Impulse #1"
  ei-model-type: float32
  ei-project-id: "1036858"
  source: edgeimpulse

After pasting press Ctrl O to save, enter and Ctrl X to exit.
- The model will then be within the Video Image Classification Brick. You may need to reload APP Lab 

