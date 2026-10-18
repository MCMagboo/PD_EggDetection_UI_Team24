# This is a Project Design of Students in TECHNOLOGICAL INSTITUTE OF THE PHILIPPINES COMPUTER ENGINEERING DEPARTMENT TEAM24

# Duck Egg Inspector

Touchscreen app for the duck egg micro-fissure inspection + UV-C station (Raspberry Pi 5).
It inspects a 10-egg tray, flags cracked eggs (screen, LED, buzzer), shows the optical, infrared and
XGrad-CAM heatmap images, and runs UV-C disinfection.

The UI is built with [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) and laid out for an
800x480 touchscreen. It scales to larger screens such as 1024x600.

## Quick start (any PC, no hardware)

```bash
pip install customtkinter pillow numpy
# run from the folder that CONTAINS egg_inspector/
python -m egg_inspector --windowed --fast
```

The default `mock` backend generates fake candled-egg images, so the full flow works without a camera,
GPIO or model.

| Option | Meaning |
|---|---|
| `--backend mock\|pi` | `mock` = fake data (default); `pi` = real camera, GPIO, UV-C relay and model |
| `--no-model` | With `--backend pi`: real camera, motors, LEDs, buzzer and UV-C; crack verdicts simulated until the model is ready |
| `--fake-vision` | With `--backend pi`: like `--no-model` but with drawn eggs instead of the camera |
| `--windowed` | Run in a window instead of fullscreen |
| `--size 1024x600` | Window size when `--windowed` |
| `--fast` | Speed up the mock backend (demos/tests) |
| `--seed N` | Repeatable mock results |

## Testing the hardware on the Pi (no install needed)

Use this to bring up the motors, lights, sensors and UV-C before the model is ready. Run it straight from
VS Code (Remote-SSH or a terminal on the Pi). The desktop-app install below is **not** needed for this.

### 1. Set the pins and relay polarity first

In `config.py`:

- **`PINS`:** set every BCM pin number to match your wiring. The defaults are placeholders.
- **`ACTIVE_HIGH`:** set to `False` for any relay or driver board that switches **on when its input is LOW**.
  Many cheap relay modules work this way.

**Get `ACTIVE_HIGH` right before connecting the UV-C lamp.** With the wrong setting, "off" means on, and the
lamp would switch on as soon as the script starts.

### 2. Copy the folder to the Pi and install the two dependencies

Copy the `egg_inspector` folder to the Pi, e.g. `~/egg_inspector`. Raspberry Pi OS already has `gpiozero`,
`numpy` and Pillow. For the touchscreen app (step 4) you also need CustomTkinter, installed in a venv that can
see the system packages:

```bash
cd ~
python3 -m venv --system-site-packages ~/egg-venv
~/egg-venv/bin/pip install customtkinter
```

In VS Code, select `~/egg-venv/bin/python` as the interpreter.

### 3. Test one device at a time

```bash
cd ~                                   # the folder that CONTAINS egg_inspector/
python3 -m egg_inspector.hwtest
```

It prints the pin map and asks you to confirm it before touching any output. Then it shows a menu:

| Menu item | What to check |
|---|---|
| Sensors (live) | The door switch reads CLOSED/open; the home and slot sensors change when you turn the tray by hand |
| Backlight on/off | The transillumination LEDs switch |
| Defect LEDs 1-10 | Each LED lights in order: LED *n* should be at holder *n* |
| Buzzer | Double beep |
| Tray: jog | Moves the tray for up to 3 s **without sensors**, e.g. to check direction before the sensors are wired. Jog back the other way so the wiring doesn't wind up |
| Tray: home | Reverses until the home sensor triggers (holder 1 at the camera) |
| Tray: next holder | Moves forward one holder and stops on the slot sensor. Check the holder gear meshes with the spin motor |
| Spin: one full turn | Runs the spin motor for `spin_seconds`. Mark the holder: it should turn **exactly once**. If not, adjust `spin_seconds` |
| Spin: step sequence | The 8 stop-start steps used during inspection |
| UV-C: short test | Max 10 s; you must type `UVC` to confirm. Refuses with the door open, and switches off if the door opens mid-test |
| Camera: test photo | Turns the backlight on and saves `~/egg_camera_test.jpg` at inspection resolution. Open it to check focus, framing and exposure |

- **Ctrl+C during a test** switches everything off and returns to the menu. Ctrl+C at the menu quits.
- **After every test** the motors, UV-C and buzzer are switched off.
- **Do not look at the UV-C lamp.** Keep the chamber closed during the UV-C test.

### Camera: Sony IMX219 (Raspberry Pi Camera Module v2)

The app opens the camera with Picamera2, which supports the IMX219 directly; no driver install is needed.

- **Laptop webcams don't work this way:** a webcam on a PC is opened with a different library (OpenCV), so
  the Pi camera code can't run on a PC. On a PC, use the mock backend.
- **Resolution:** photos are 1640×1232, one of the IMX219's own built-in modes. It uses the full field of view
  at a lower resolution, so captures are fast. If hairline cracks are too small to see, use the full 3280×2464
  by changing the `size` in `PiBackend._init_camera` (and in the hardware test's camera item). Each photo and
  model run will then be slower.
- **Focus is manual:** the IMX219 has no autofocus, and its lens comes set for distant subjects, so an egg a few
  centimetres away starts out blurry. Take a test photo (menu item **Camera: test photo**), open
  `~/egg_camera_test.jpg`, and gently turn the lens with the focus tool or tweezers until the shell texture is
  sharp. **Do this before collecting training images.**
- **Pi 5 cable:** the Pi 5 has a smaller camera socket than older Pis, so it needs a **22-pin to 15-pin** ribbon
  cable; the one that comes with the camera won't fit. Power off before connecting.
- **If `rpicam-hello --list-cameras` finds nothing:**
  - **Official module:** check the cable and which way round it's plugged in. It's detected automatically.
  - **Third-party IMX219 board (e.g. Arducam):** add `camera_auto_detect=0` and `dtoverlay=imx219` to
    `/boot/firmware/config.txt`, then reboot.
- **NoIR version:** photos look pinkish in room light. That's normal and fine under your own backlight. It isn't
  a replacement for the infrared camera, which is a separate sensor.
- **Exposure and white balance are automatic for now:** a backlit egg can come out brighter or darker depending
  on its shell, and the model sees those differences too. Before the model goes in, lock the exposure, gain and
  white balance, and use the same settings for training photos as for photos the app scores. See *Known open items*.

### 4. Run the whole app on the real rig (before the model is ready)

When each device works on its own:

```bash
cd ~
~/egg-venv/bin/python -m egg_inspector --backend pi --no-model
```

This runs a complete batch through the touchscreen app with the **real camera, tray, spin motor, LEDs, buzzer
and UV-C**:

1. The tray homes in reverse.
2. For each holder: the tray moves forward, the egg spins in steps, and the camera takes 8 photos.
3. The defect LED and buzzer signal any rejects.
4. The tray reverses to holder 1.
5. UV-C runs automatically if a crack was found (door must be closed).

Only the **crack verdicts are simulated** until the model is integrated: about one egg in four gets flagged so the
reject and UV-C paths run. The header shows **"Simulated results: no model loaded"** in amber the whole time. The
infrared panel shows "Infrared camera not connected" until `_thermal_frame` is written.

- Add `--windowed` to run in a window instead of fullscreen (e.g. over VS Code Remote / VNC).
- Use `--fake-vision` instead of `--no-model` to also replace the camera with drawn eggs, e.g. while the camera
  is unplugged.
- Without either flag, the app needs the trained model. It stops with a message saying what isn't written yet.
- **No camera found:** the app stops with a message. Power off, check the ribbon cable, then run
  `rpicam-hello --list-cameras`.
- **Tray faults:** the app shows a fault if the home or slot sensor never triggers. Check them with the
  Sensors item in the test menu.

## Install on the Raspberry Pi (desktop app)

*Not needed yet.* This is for when the model is integrated and the station is ready for operators.

1. Copy the whole `egg_inspector` folder somewhere permanent, e.g. `~/egg_inspector`. Keep that folder name;
   the icon runs the app from there.
2. Install:
   ```bash
   cd ~/egg_inspector
   bash deploy/install.sh
   ```
3. Double-tap **Egg Inspector** on the desktop (also in Menu > Accessories).

Re-run the installer to change options:

| Option | Meaning |
|---|---|
| *(none)* | Mock backend; works before the model and hardware are ready |
| `--backend pi` | Real hardware + model (needs the TODOs in `pi_backend.py` filled in first) |
| `--autostart` | Open the app automatically at login (kiosk mode) |
| `--no-apt` | Skip `apt-get` (offline or already installed) |
| `--uninstall` | Remove icons, autostart entry and venv; keeps the source folder |

The installer installs the system packages, creates a venv at `~/.local/share/egg_inspector/venv`
(with access to the apt-installed `picamera2`/`gpiozero`), and adds the desktop and menu icons.

- **Logs:** `~/.local/state/egg_inspector/app.log`. If the app crashes, an error box shows the last lines.
- **Single instance:** a second tap on the icon while the app is open does nothing.
- **Exit:** the **Exit** button (top right) or Esc. Exit is disabled during an inspection or UV-C run.
- **Font:** the installer adds the Inter font if apt has it; otherwise the UI uses DejaVu Sans.

## Operating the station

| Control | What it does |
|---|---|
| **Start** | Inspects all 10 eggs, then runs UV-C if cracks were found (see below) |
| **Stop** | Stops inspection or UV-C immediately |
| **Reset** | Clears the screen and the defect LEDs |
| **Tray eggs** | Tap an inspected egg to review its images and result. The egg being inspected is outlined in blue |
| **Run UV-C** | Manual UV-C at any time. Tap once to arm (turns amber), tap again within 3 s to start |
| **Auto** switch | In the UV-C card: start UV-C automatically when a cracked egg is found |

### UV-C behaviour

- **Cracks found + Auto on:** UV-C starts automatically after inspection.
- **Cracks found + Auto off:** UV-C shows **Pending**; the operator taps Run UV-C.
- **No cracks:** UV-C is skipped (**Not needed**).
- **Default:** the switch's starting position is `auto_uvc` in `config.py`.

Safety rules in software:
- UV-C never starts with the chamber door open (**Blocked**).
- It stops within about 50 ms of Stop or the door opening (**Interrupted**).
- It is always switched off when a run ends, including after errors.

**The hardware door interlock must also cut UV-C power on its own.** Don't rely on the software alone.

## Project layout

| File | Purpose |
|---|---|
| `__main__.py` | Command-line entry point; picks the backend |
| `config.py` | All settings: egg count, UV-C time, threshold, screen, GPIO pins, relay polarity |
| `hwtest.py` | Hardware bring-up menu: test each motor, light, sensor and UV-C on the Pi |
| `ui.py` | CustomTkinter touchscreen UI (main thread only) |
| `controller.py` | Background worker: inspection cycle, UV-C policy and safety checks |
| `backend_base.py` | `Backend` interface that both backends implement |
| `mock_backend.py` | Fake images for development without hardware |
| `pi_backend.py` | Real camera, GPIO, UV-C relay and model (**model parts still TODO**). Also `NoModelPiBackend` (real camera + hardware, simulated verdicts) and `FakeVisionPiBackend` (no camera) |
| `datatypes.py` | `EggResult`, `Summary`, `UvcOutcome`, `Event` |
| `deploy/` | Pi installer, launcher script, icon, pip requirements |

The UI never talks to hardware directly. The controller runs on a worker thread, calls the backend, and
sends events to the UI through a queue. You can swap the mock for the real backend without UI changes.

## How each egg is inspected

The camera is fixed on its stand and inspects one egg at a time. The eggs stand upright in 10 geared holders on
a rotating tray. The tray is turned by a motor on the centre shaft. A separate spin motor beside the camera stand
meshes with the gear of whichever holder is at the camera station.

0. **Home:** before the batch, the tray reverses to holder 1 if it isn't already there
   (e.g. after a Stop or power cut).
1. **Tray moves:** the centre-shaft motor turns the tray forward (36° per slot) until the next holder is at the
   camera, aligned so its gear meshes with the spin motor.
2. **Egg spins:** the spin motor turns the egg about its vertical axis, 360° in `frames_per_egg` equal steps
   (default 8, i.e. every 45°). It stops and settles before each capture, so hairline cracks are not motion-blurred.
3. **Capture:** one optical and one infrared frame per step.
4. **Score:** the model scores every view. The egg is **rejected if any view** scores at or above `detect_threshold`.
5. **Display:** the screen shows the highest-scoring view, its heatmap, and the angle where the crack faced the
   camera (e.g. "Micro-fissure at 135° · 8 views").
6. **Return:** after egg 10, the tray **reverses** back to holder 1, then UV-C runs if needed.

### Tray direction and wiring

The tray only moves **forward** during a batch (holder 1 → 10, 324°) and returns in **reverse**. It never turns
a full revolution in one direction, so the wiring on the tray cannot wind up.

- **Stop or fault mid-batch:** the tray stops where it is. The next Start reverses it home before inspecting.
- **Guards:** the code refuses to move backwards between holders, or to move at all before the tray is homed.
- **Hardware needed:**
  - A reversible centre-shaft motor (H-bridge, pins `tray_motor_fwd` / `tray_motor_rev`).
  - A **home sensor** at holder 1 (`tray_home`).
  - A **slot sensor** that triggers when any holder is aligned at the camera (`tray_slot`), e.g. one magnet per
    holder and a hall sensor.
- **Faults:** if a sensor never triggers within `tray_move_timeout` / `tray_home_timeout`, the motor stops and the
  screen shows a fault instead of the tray turning forever.

Spin settings in `config.py`:

| Setting | Meaning |
|---|---|
| `frames_per_egg` | Views per full turn. More views catch smaller cracks but take longer. |
| `spin_seconds` | Time for the spin motor to turn an egg exactly once (depends on the gear ratio). **Calibrate on the real rig.** |
| `settle_seconds` | Pause after each step before the photo. |

The spin step is time-based, for a DC motor on a relay (`spin_motor` pin). With a stepper motor, change
`PiBackend._spin_step` to drive a fixed number of steps instead.

**The two motors never run at the same time.** Spinning while the tray moves would grind the gears, so
`_spin_step` refuses to start if the tray motor is still on.

**Tray alignment matters:** if a holder stops slightly off position, its gear won't mesh and the egg won't turn.
The tray stops when the slot sensor triggers, so place the sensor where the gears mesh. If the tray overshoots,
slow the motor or add a mechanical detent. A stepper on the centre shaft would also give repeatable 36° steps.

**Time per egg:** about `spin_seconds + frames_per_egg × (settle + capture + inference)`. With 8 views and a model
that takes 1 s per image on the Pi, expect roughly 12–13 s per egg, or about 2 minutes per tray. Measure it on
the Pi and lower `frames_per_egg` if that's too slow.

## Integrating the trained model

Fill in these methods in `pi_backend.py`. Tray homing and indexing, and the spin and capture sequence, are
already written:

| Method | Must do |
|---|---|
| `_load_model()` | Load the trained SOLOv2 weights |
| `_thermal_frame()` | Return a frame from the infrared camera as a PIL image |
| `_predict(optical)` | Run the model on **one view**; return its max crack score (0–1). The threshold is applied for you |
| `_explain(optical)` | XGrad-CAM on ResNet-50 layer 3, upsampled and overlaid on `optical` (PIL image) |

Checklist when the model is ready:

- **Threshold:** set `detect_threshold` in `config.py` to the value calibrated on your validation set. Because an
  egg is rejected if *any* of its views crosses it, check the false-reject rate per egg, not per image.
- **Preprocessing:** must match training. The IMX219 captures 1640x1232 RGB (configured in
  `PiBackend._init_camera`); check resolution, crop, colour order and normalisation against your training pipeline.
- **Camera settings:** train on photos taken by the station's own IMX219, with the same focus, backlight, exposure
  and white balance as during inspection.
- **Dependencies:** add PyTorch and your detection framework to `deploy/requirements.txt`. Test installing them
  on the Pi early; mmcv in particular can be hard to build on ARM.
- **Confidence display:** for a cracked egg the screen shows the raw crack score. With a low threshold, a correct
  reject can display a low percentage, so consider showing a calibrated value.
- **Speed:** inference runs on the worker thread, so slow models won't freeze the UI. Measure per-egg time on the
  Pi 5; it is shown as "Inspection time" after each batch.

### Known open items

- Camera exposure, gain and white balance are automatic. Once the backlight and focus are final, lock them to
  fixed values so every egg is photographed the same way. This isn't built yet; the plan is new settings in
  `config.py` applied in `PiBackend._init_camera`.

- `PiBackend` imports the camera and GPIO libraries on start-up. The model therefore can't be tested on a PC
  without hardware yet. Splitting the model into its own module is planned.
- `spin_seconds` is a placeholder until it's measured on the real spin motor and gears.
- The tray code assumes a reversible motor plus home and slot sensors (see *Tray direction and wiring*). If the
  rig uses a stepper instead, `home_tray` and `_index_tray` in `pi_backend.py` need adapting.
- `eggs_loaded()` always returns `True`; it needs a tray-presence sensor or camera check.
- GPIO pin numbers in `config.py` are placeholders; match them to the schematic.
- `ACTIVE_HIGH` in `config.py` must match each relay board (see *Testing the hardware on the Pi*).
