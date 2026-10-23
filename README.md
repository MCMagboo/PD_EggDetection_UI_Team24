# This is a Project Design of Students in TECHNOLOGICAL INSTITUTE OF THE PHILIPPINES COMPUTER ENGINEERING DEPARTMENT TEAM24

# Duck Egg Inspector

Touchscreen app for the duck egg micro-fissure inspection + UV-C station (Raspberry Pi 5).
It inspects a 10-egg tray, flags cracked eggs (screen, LED, buzzer), shows the optical and
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
| `--backend mock\|pi\|images\|webcam` | `mock` = fake data (default); `pi` = real camera, GPIO, UV-C relay and the model; `images` = your photos through the real model, no hardware; `webcam` = USB webcam with a Live test button |
| `--images DIR` | With `--backend images`: folder of egg photos, one per tray slot |
| `--camera 0` | With `--backend webcam`: webcam index or path, e.g. `/dev/video8` |
| `--model local\|api` | Crack model: `local` = `model_best_quantized.pth` on this machine (default); `api` = the Modal model API |
| `--no-model` | With `--backend pi`: real camera, motors, LEDs, buzzer and UV-C; crack verdicts simulated (no network needed) |
| `--fake-vision` | With `--backend pi`: like `--no-model` but with drawn eggs instead of the camera |
| `--windowed` | Run in a window instead of fullscreen |
| `--size 1024x600` | Window size when `--windowed` |
| `--fast` | Speed up the mock backend (demos/tests) |
| `--seed N` | Repeatable mock results |

## Testing the hardware on the Pi (no install needed)

Use this to bring up the motors, lights, sensors and UV-C without the model. Run it straight from
VS Code (Remote-SSH or a terminal on the Pi). The desktop-app install below is **not** needed for this.

### 1. Set the pins and relay polarity first

In `config.py`:

- **`PINS`:** set every BCM pin number to match your wiring. The defaults are placeholders.
- **`ACTIVE_HIGH`:** set to `False` for any relay or driver board that switches **on when its input is LOW**.
  Many cheap relay modules work this way.

**Get `ACTIVE_HIGH` right before connecting the UV-C lamp.** With the wrong setting, "off" means on, and the
lamp would switch on as soon as the script starts.

### 2. Copy the folder to the Pi and install the dependencies

Copy the `egg_inspector` folder to the Pi, e.g. `~/egg_inspector`. Raspberry Pi OS already has `gpiozero`,
`numpy` and Pillow. For the touchscreen app (step 4) you also need CustomTkinter, installed in a venv that can
see the system packages:

```bash
cd ~
python3 -m venv --system-site-packages ~/egg-venv
~/egg-venv/bin/pip install -r ~/egg_inspector/deploy/requirements.txt
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
- **NoIR version:** photos look pinkish in room light. That's normal and fine under your own backlight.
- **Exposure and white balance are automatic for now:** a backlit egg can come out brighter or darker depending
  on its shell, and the model sees those differences too. Before the model goes in, lock the exposure, gain and
  white balance, and use the same settings for training photos as for photos the app scores. See *Known open items*.

### 4. Run the whole app on the real rig (simulated verdicts)

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

With `--no-model`, only the **crack verdicts are simulated**: about one egg in four gets flagged so the
reject and UV-C paths run. The header shows **"Simulated results: no model loaded"** in amber the whole time.

- Add `--windowed` to run in a window instead of fullscreen (e.g. over VS Code Remote / VNC).
- Use `--fake-vision` instead of `--no-model` to also replace the camera with drawn eggs, e.g. while the camera
  is unplugged.
- Without either flag, the app runs the trained model on every view (see *The crack-detection model*). It
  stops with a message if `model_best_quantized.pth` or PyTorch is missing.
- **No camera found:** the app stops with a message. Power off, check the ribbon cable, then run
  `rpicam-hello --list-cameras`.
- **Tray faults:** the app shows a fault if the home or slot sensor never triggers. Check them with the
  Sensors item in the test menu.

## Deploy on the Raspberry Pi

Needs **64-bit** Raspberry Pi OS (PyTorch has no 32-bit build): `uname -m` must print `aarch64`.
Commands are Pi bash unless marked PowerShell.

1. **Get the code and the model.** The model (`model_best_quantized.pth`, 88 MB) is in the repository, so this brings it
   too. The folder must be named `egg_inspector`, because the icon runs the app from there:
   ```bash
   git clone https://github.com/MCMagboo/PD_EggDetection_UI_Team24.git ~/egg_inspector   # first time
   cd ~/egg_inspector && git pull                                                          # later updates
   ```
2. **Install the packages, PyTorch included.** The download is large and takes a while:
   ```bash
   python3 -m venv --system-site-packages ~/egg-venv      # skip if it already exists
   ~/egg-venv/bin/pip install -r ~/egg_inspector/deploy/requirements.txt
   ```
3. **Time the model on the Pi.** Take a photo with the test menu's **Camera: test photo**, then:
   ```bash
   cd ~
   ~/egg-venv/bin/python -m egg_inspector.model_client ~/egg_camera_test.jpg --heatmap heat.png
   ```
   It prints the verdict and the seconds taken. Each egg takes about `frames_per_egg` × that time. If it's too
   slow, lower `frames_per_egg` in `config.py`, or use the model API (`--model api`, see *The crack-detection
   model*).
4. **Check the wiring settings before connecting the UV-C lamp:** `PINS` and `ACTIVE_HIGH` in `config.py` must
   match the schematic and relay boards (see *Testing the hardware on the Pi*). Also calibrate `spin_seconds`.
5. **Run one full tray from the terminal:**
   ```bash
   cd ~
   ~/egg-venv/bin/python -m egg_inspector --backend pi
   ```
6. **Install the desktop app** once step 5 works:
   ```bash
   cd ~/egg_inspector
   bash deploy/install.sh --backend pi          # add --autostart to open it at login (kiosk)
   ```
   Then double-tap **Egg Inspector** on the desktop (also in Menu > Accessories). The installer makes its own
   venv at `~/.local/share/egg_inspector/venv` and warns if `model_best_quantized.pth` is missing.

Re-run the installer to change options:

| Option | Meaning |
|---|---|
| *(none)* | Mock backend; works before the model and hardware are ready |
| `--backend webcam` | USB webcam + Live test, real model, no motors. Finds the webcam itself, or add `--camera /dev/videoN` |
| `--backend pi` | The station: real hardware + the trained model (needs `model_best_quantized.pth` + PyTorch, see *The crack-detection model*) |
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
| **Optical / XGrad-CAM image** | Tap to see it full size (the images are small on a 7" screen). Tap again to close. Start/Stop/Reset stay visible |
| **Run UV-C** | Manual UV-C at any time. Tap once to arm (turns amber), tap again within 3 s to start |
| **Auto** switch | In the UV-C card: start UV-C automatically when a cracked egg is found |

### Screen

The station uses the **Waveshare 7inch HDMI LCD (B)**: 800x480, HDMI video plus USB touch. The app is laid out
for exactly this size. It should look sharp only if the Pi outputs **800x480** itself. If the Pi sends a bigger
mode, the LCD scales the picture down and everything goes soft.

- Check the resolution: `wlr-randr` (Wayland desktop) or `xrandr` (X11) should show `800x480` as current.
- If it doesn't, add `video=HDMI-A-1:800x480M@60` to the end of the single line in `/boot/firmware/cmdline.txt`,
  then reboot. Use `HDMI-A-2` if the screen is on the second micro-HDMI port.
- Keep desktop scaling at 100%. Raspberry Pi OS options like "large screen" or pixel doubling would enlarge the
  app as a bitmap and blur it.
- To preview the exact screen on a PC: `python -m egg_inspector --windowed` (800x480 is the default size).

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
| `image_backend.py` | `--backend images` (photo folder) and `--backend webcam` (USB webcam, Live test): the real model, no hardware |
| `pi_backend.py` | Real camera, GPIO, UV-C relay; sends views to the model. Also `NoModelPiBackend` (real camera + hardware, simulated verdicts) and `FakeVisionPiBackend` (no camera) |
| `local_model.py` | Runs `model_best_quantized.pth` (Mask R-CNN + XGrad-CAM) on this machine; the default |
| `model_best_quantized.pth` | The trained model weights (88 MB, half precision), used by the local model |
| `model_client.py` | Client for the model API, `load_model()` (picks local or API), and a one-photo command-line check |
| `api_contract.py` | The API's response format, copied unchanged from the model team's client |
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
3. **Capture:** one optical frame per step. It goes to the model API straight away, so inference runs while the
   egg spins to the next angle (up to `model_parallel` views in flight).
4. **Score:** the model scores every view. The egg is **rejected if any view** scores at or above the threshold.
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

## Testing with a USB webcam (Live test)

`--backend webcam` runs the normal app with a USB webcam instead of the station camera, and no motors. LEDs,
buzzer and UV-C are only logged.

- **Live** (button next to Exit): the Optical panel follows the camera, and the model checks the newest frame
  non-stop. The result card and the XGrad-CAM panel update after each check: about 5 s on a laptop, longer on
  a Pi CPU. Tap **Live** again to stop.
- **Start:** the normal tray cycle. Each slot takes the newest webcam frame, so swap the egg between slots.
  Pressing Start switches Live off.

On the Pi (bash), after the *Deploy on the Raspberry Pi* steps 1–3:

```bash
sudo apt install -y python3-opencv v4l-utils     # the installer does this too with --backend pi
v4l2-ctl --list-devices                           # find the webcam's /dev/videoN
cd ~
~/egg-venv/bin/python -m egg_inspector --backend webcam --camera /dev/video0
```

Use the webcam's own path from `v4l2-ctl` (on a Pi 5 it isn't always `/dev/video0`). Add `--windowed` to run in
a window, or `--model api` to use the model API.

To open webcam mode from the **desktop icon**, run the installer with it. It finds the USB webcam by itself;
add `--camera /dev/videoN` to choose one:

```bash
cd ~/egg_inspector
bash deploy/install.sh --backend webcam
```

The header must say "Test mode: webcam …" and show a **Live** button. If you see drawn orange eggs, or
"Simulated results", the icon or command is still in `mock` or `--no-model` mode.

On a PC (PowerShell): `pip install opencv-python-headless`, then
`python -m egg_inspector --backend webcam --camera 0 --windowed`.

## The crack-detection model

The model is **Mask R-CNN (ResNet-50 FPN) + XGrad-CAM**, trained by the model team. It can run two ways, chosen by
`model_source` in `config.py` or `--model local|api`. Both give the same answers:

| | `local` (default) | `api` |
|---|---|---|
| Where it runs | On this machine (Pi or PC), CPU | Model team's Modal GPU server |
| Needs | `model_best_quantized.pth` (in the repository) + PyTorch | Internet + Modal token pair |
| Speed per view (laptop) | ~4 s, ~7 s when a crack is found (heatmap) | ~3 s warm; 15 s to minutes when the server is cold |
| Code | `local_model.py` | `model_client.py` (replies checked against `api_contract.py`) |

`model_best_quantized.pth` is the checkpoint the API serves (`model_best.pth`, epoch 14, SHA-256 `f3e4c058…c8b9c`), stored
with 16-bit numbers. Each view gives:

- the crack decision and the max crack score (cracked if it is at or above the threshold, 0.83 by default),
- an XGrad-CAM overlay on `backbone.body.layer4[-1]` when a crack is found. This is shown in the XGrad-CAM panel.

### Local model (default)

The weights come with the repository: `model_best_quantized.pth` (88 MB). It is `model_best.pth` with every number stored
as a 16-bit float instead of 32-bit, so half the size. On the dataset photos it gives the same verdicts as the
full file, with scores within 0.001. Only PyTorch needs installing.

The full `model_best.pth` (176 MB) is too big for GitHub (the limit is 100 MB per file), so `.gitignore` keeps it
out. To use it locally, put it next to `config.py` and set `model_path = "model_best.pth"`. Keep later model
updates rare: every new version of the weights adds another 88 MB to the repository's history.

On the Pi (bash):

```bash
~/egg-venv/bin/pip install -r ~/egg_inspector/deploy/requirements.txt
cd ~
~/egg-venv/bin/python -m egg_inspector.model_client ~/egg_camera_test.jpg --heatmap heat.png
~/egg-venv/bin/python -m egg_inspector --backend pi
```

On a PC (PowerShell), from the folder that contains `egg_inspector\`. The `--index-url` gets the CPU build,
which is much smaller than the default GPU build:

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r egg_inspector\deploy\requirements.txt
python -m egg_inspector.model_client "C:\path\to\egg.jpg" --heatmap heat.png
```

The check prints `CRACK` or `no crack` with the score, and saves the heatmap if a crack was found.

About the checkpoint: it holds `{"model": weights, "epoch", "validation_ap", "config_sha256"}`. Its mask-head
layers are named `deconv` / `mask_logits` instead of torchvision's `conv5_mask` / `mask_fcn_logits`.
`local_model.py` renames them on load; every layer then matches exactly. The file is opened with
`weights_only=True`, so only tensors are read and no code inside it runs.

### Using the model API instead (optional)

Run with `--model api`, or set `model_source = "api"`. The API needs a Modal proxy token pair (a key starting
`wk-` and a secret starting `ws-`); ask the model team for it.

On the Pi, put it in a private file:

```bash
mkdir -p ~/.config/egg_inspector
nano ~/.config/egg_inspector/model.env
chmod 600 ~/.config/egg_inspector/model.env
```

```bash
MASKRCNN_API_KEY=wk-...
MASKRCNN_API_SECRET=ws-...
# MASKRCNN_API_URL=https://...   (only if the endpoint changes; default is model_api_url in config.py)
```

The desktop icon (`deploy/run.sh`) loads this file automatically. In a terminal, load it first:

```bash
cd ~
set -a; . ~/.config/egg_inspector/model.env; set +a
~/egg-venv/bin/python -m egg_inspector --backend pi --model api
```

On a PC (PowerShell):

```powershell
$env:MASKRCNN_API_KEY = 'wk-...'
$env:MASKRCNN_API_SECRET = 'ws-...'
python -m egg_inspector.model_client "C:\path\to\egg.jpg" --model api --heatmap heat.png
```

**Keep the token pair private.** Never commit it (`.gitignore` already blocks `*.env` files), and don't put
it in a shared or public folder or chat. Anyone who has it can use the model server. If it leaks, ask the
model team to create a new pair and delete the old one, then update `model.env`.

The client retries a dropped connection or a busy server (502/503/504) up to 3 times before showing an error.

### Testing the real model in the app on a PC

`--backend images` runs the whole app on a laptop with **real model results**. Nothing is simulated. Put up to
10 egg photos (.jpg/.png, or iPhone .HEIC after `pip install pillow-heif`) in a folder; each tray slot gets one
photo, in file-name order. The screen shows each photo's real verdict, confidence and XGrad-CAM heatmap. There are
no motors, so LEDs, buzzer and UV-C are only logged in the terminal.

```powershell
cd C:\Users\<you>\OneDrive\Desktop
python -m egg_inspector --backend images --images "C:\path\to\photos" --windowed
```

Add `--model api` to use the API instead (set the two `$env:` variables first). The header shows
"Test mode: N photos from <folder>, local model" (or "model API"). The result card shows which photo each egg is.
Photos larger than 2048 px are scaled down first; the API rejects bigger images (HTTP 413).

### Settings and timing (`config.py`)

| Setting | Meaning |
|---|---|
| `model_source` | `"local"` (default) or `"api"` |
| `model_path` | Local weights file; relative paths are inside the `egg_inspector` folder |
| `detect_threshold` | `None` uses the model's validated threshold (0.83). A number from 0.01 to 0.99 overrides it. Because an egg is rejected if *any* view crosses it, check false rejects per egg, not per image. |
| `model_api_url` | Base URL of the deployed API (without `/predict`) |
| `model_parallel` | API only: views sent at once; 4 lets uploads overlap the spin |

- **Speed on the Pi (local):** not measured yet. A Pi 5 CPU is several times slower than a laptop, so expect
  roughly 10–30 s per view. With 8 views per egg that is minutes per egg. Measure one tray. If it's too slow, lower
  `frames_per_egg`, or use `--model api` on the Pi.
- **Network:** only the API needs internet. If it can't be reached, the batch stops with an error on screen.
- **Camera settings:** the model should see photos like the ones it was trained on: same focus, backlight,
  exposure and white balance. Share some photos from the station's IMX219 with the model team.

### Known open items

- Camera exposure, gain and white balance are automatic. Once the backlight and focus are final, lock them to
  fixed values so every egg is photographed the same way. This isn't built yet; the plan is new settings in
  `config.py` applied in `PiBackend._init_camera`.

- `spin_seconds` is a placeholder until it's measured on the real spin motor and gears.
- The tray code assumes a reversible motor plus home and slot sensors (see *Tray direction and wiring*). If the
  rig uses a stepper instead, `home_tray` and `_index_tray` in `pi_backend.py` need adapting.
- `eggs_loaded()` always returns `True`; it needs a tray-presence sensor or camera check.
- GPIO pin numbers in `config.py` are placeholders; match them to the schematic.
- `ACTIVE_HIGH` in `config.py` must match each relay board (see *Testing the hardware on the Pi*).
