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
| `--camera 0` | USB webcam index or path, e.g. `/dev/video8`. With `--backend webcam` (default `0`), or with `--backend pi` to use a USB webcam instead of the ribbon camera |
| `--fps 30` | Camera frame rate, fixed (default `camera_fps` in `config.py`) |
| `--model local\|api` | Crack model: `local` = `model_best.pth` on this machine, or `model_best_quantized.pth` if that isn't there (default); `api` = the Modal model API |
| `--no-model` | With `--backend pi`: real camera, candling light, LEDs, buzzer and UV-C; crack verdicts simulated (no network needed) |
| `--fake-vision` | With `--backend pi`: like `--no-model` but with drawn eggs instead of the camera |
| `--windowed` | Run in a window instead of fullscreen |
| `--size 1024x600` | Window size when `--windowed` |
| `--fast` | Speed up the mock backend (demos/tests) |
| `--seed N` | Repeatable mock results |

## Testing the hardware on the Pi (no install needed)

Use this to bring up the candling light, defect LEDs, buzzer, door switch and UV-C without the model. Run it straight from
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
mkdir -p ~/egg-tmp && TMPDIR=~/egg-tmp ~/egg-venv/bin/pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu   # CPU-only build
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
| Door switch (live) | Reads CLOSED/open as you open and close the door |
| Backlight on/off | The transillumination LEDs switch |
| Defect LEDs 1-10 | Each LED lights in order: LED *n* should be at holder *n* |
| Buzzer | Double beep |
| UV-C: short test | Max 10 s; you must type `UVC` to confirm. Refuses with the door open, and switches off if the door opens mid-test |
| Camera: test photo | Turns the backlight on and saves `~/egg_camera_test.jpg` at inspection resolution. Open it to check focus, framing and exposure |

- **Ctrl+C during a test** switches everything off and returns to the menu. Ctrl+C at the menu quits.
- **After every test** UV-C and the buzzer are switched off. The candling light stays as you set it.
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

This runs a complete batch through the touchscreen app with the **real camera, candling light, LEDs, buzzer
and UV-C** (no motors yet, see *How each egg is inspected*):

1. For each of the 10 slots: place the egg in front of the camera during the countdown. The app takes the
   sharpest picture of half a second and checks it.
2. The defect LED and buzzer signal any rejects.
3. UV-C runs automatically if a crack was found (door must be closed).

With `--no-model`, only the **crack verdicts are simulated**: about one egg in four gets flagged so the
reject and UV-C paths run. The header shows **"Simulated results: no model loaded"** in amber the whole time.

- Add `--windowed` to run in a window instead of fullscreen (e.g. over VS Code Remote / VNC).
- Use `--fake-vision` instead of `--no-model` to also replace the camera with drawn eggs, e.g. while the camera
  is unplugged.
- Without either flag, the app runs the trained model on every view (see *The crack-detection model*). It
  stops with a message if `model_best_quantized.pth` or PyTorch is missing.
- **No camera found:** the app stops with a message. Power off, check the ribbon cable, then run
  `rpicam-hello --list-cameras`.

## Deploy on the Raspberry Pi

Needs **64-bit** Raspberry Pi OS (PyTorch has no 32-bit build): `uname -m` must print `aarch64`.
Commands are Pi bash unless marked PowerShell.

### Step by step: what to type in the Pi terminal

For our station: **Raspberry Pi 5 (8 GB), 32 GB SD card, USB webcam, Waveshare 7" screen**. Open a terminal on the
Pi's own desktop (Menu > Accessories > Terminal) and type each line, pressing Enter after it. A line starting with
`#` is a note; you don't need to type it. Steps 1-6 need **internet**. After that, the Pi runs offline.

**1. Check the system** (once):

```bash
uname -m
echo $XDG_SESSION_TYPE
```

You should see `aarch64`, and then `wayland` or `x11`. If not, see *Before you start* below.

**2. Update the Pi and add the tools** (once; takes a few minutes; asks for your password):

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y git curl
sudo reboot
```

The Pi restarts. Open the terminal again.

**3. Plug in the USB webcam and check it:**

```bash
lsusb
```

The webcam must be in the list (e.g. "Webcam", "Camera", or its brand).

**4. Get the app and the model.**

First time, when there is no `egg_inspector` folder yet:

```bash
git clone https://github.com/MCMagboo/PD_EggDetection_UI_Team24.git ~/egg_inspector
```

If the folder is already there from before:

```bash
cd ~/egg_inspector
git stash          # sets aside any local edits so the update can't clash with them
git pull
cd ~
```

Check: `ls -lh ~/egg_inspector/model_best_quantized.pth` shows about `85M`.

**5. Install everything** (once; about 10-20 minutes; downloads about 2 GB):

```bash
bash ~/egg_inspector/deploy/install.sh --backend webcam
```

It installs the system and Python packages (PyTorch, OpenCV), Ollama, the offline SLM `qwen2.5:1.5b` and the
desktop icon. Near the end it prints `Installing launcher (--backend webcam --camera /dev/...)`, which is the
webcam it found. It finishes with the self-check:

```text
OK    crack model file  model_best_quantized.pth (84 MB)
OK    PyTorch  ...
OK    crack model runs  loads in ... s, one photo in ... s
OK    Ollama (local SLM server)  http://127.0.0.1:11434, SLM qwen2.5:1.5b
OK    SLM writes an explanation  ...
Ready to run offline.
```

If a line says `FAIL`, fix it now while there is internet. The message on that line says what is missing.

**6. Start the app:** double-tap **Egg Inspector** on the desktop. Or from the terminal:

```bash
cd ~
~/.local/share/egg_inspector/venv/bin/python -m egg_inspector --backend webcam --camera /dev/video0
```

In the terminal command, use the `/dev/...` the installer printed in step 5. The header must say
**"Test mode: webcam …, local model"**, and there must be a **Live** button next to Exit.

**7. Test it:**

- Hold a candled egg in front of the webcam. Tap **Auto** on the Optical panel and keep the egg still for about
  5 s. The egg should show its shell, not a white "sun".
- Tap **Live**. The result card and the text beside the heatmap update every few seconds.
- Tap **Start** for the 10-slot cycle. Before each egg after the first, the status line counts down
  ("Place egg 2/10 in front of the camera (5 s)"): swap the egg then. If the picture is too bright, it waits.
- Tap the heatmap of a rejected egg to see the full explanation.

**8. Check that it works offline** (once):

```bash
nmcli radio wifi off
```

Use the app again (step 7). Then turn Wi-Fi back on: `nmcli radio wifi on`. If you use a network cable, unplug it
instead.

**Later updates** (needs internet):

```bash
bash ~/egg_inspector/deploy/update.sh
```

Don't use a plain `git pull` for updates. It fetches the files but installs nothing new.

**If something goes wrong:**

```bash
cd ~ && ~/.local/share/egg_inspector/venv/bin/python -m egg_inspector.selfcheck     # what is missing
tail -n 30 ~/.local/state/egg_inspector/app.log                                       # the app's last messages
v4l2-ctl --list-devices                                                               # webcam paths
```

Send these outputs with a photo of the screen.

### Before you start: set up the Pi from a terminal

Do these once on the Pi, in a terminal (Menu > Accessories > Terminal), **while it has internet**. The installer
then does the rest: Python packages, PyTorch, OpenCV, Ollama, the offline SLM and the desktop icon.

1. **Raspberry Pi OS 64-bit *with desktop*.** The app is a window, so it needs the desktop. The "Lite" version
   has no desktop, and 32-bit has no PyTorch. Check:
   ```bash
   uname -m                      # must print: aarch64
   echo $XDG_SESSION_TYPE        # must print: wayland or x11 (run it in the Pi's own desktop terminal)
   ```
   If either is wrong, re-flash the SD card with Raspberry Pi Imager: *Raspberry Pi OS (64-bit)*, with desktop.
2. **Internet** (Wi-Fi or cable), only for the install. `ping -c 2 github.com` must get replies.
3. **Bring the system up to date**, and install `git` and `curl` (the installer needs them):
   ```bash
   sudo apt update
   sudo apt full-upgrade -y
   sudo apt install -y git curl
   sudo reboot
   ```
4. **Enough room:**
   ```bash
   df -h /       # "Avail" at least 5 GB (the app with the SLM uses 2-3 GB)
   free -h       # "Mem" total: 8 GB gets the best SLM, 4 GB a smaller one, 2 GB none
   ```
5. **More swap on a 4 GB Pi or smaller.** Swap is disk space used as spare memory, so the PyTorch install and the
   first heatmap don't run out of memory. Skip this on an 8 GB Pi. Only if `free -h` shows less than 1 GB
   of "Swap", and only on systems that use `dphys-swapfile`:
   ```bash
   sudo dphys-swapfile swapoff
   sudo sed -i 's/^#\?CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
   sudo dphys-swapfile setup
   sudo dphys-swapfile swapon
   free -h       # "Swap" now about 2.0Gi
   ```
   If the system says `dphys-swapfile: command not found`, it manages swap differently; leave it as it is.
6. **Plug in the USB webcam** before installing, so the installer can find it. `lsusb` should list it, and
   for the station rig the ribbon camera too (`rpicam-hello --list-cameras`).
7. **The screen at 800x480** (Waveshare 7" (B)): see *Screen* under *Operating the station*.
8. **For the kiosk** (`--autostart`) only: log in to the desktop by itself at power-on:
   `sudo raspi-config` → *System Options* → *Boot / Auto Login* → *Desktop Autologin*.

Run the installer and the app from the Pi's own desktop, or a terminal on it. Over SSH, the window has nowhere to
appear. If you must start it over SSH, first run `export DISPLAY=:0` (X11) so it opens on the Pi's screen.

**Short version: two commands with internet, then the station runs offline.**

```bash
git clone https://github.com/MCMagboo/PD_EggDetection_UI_Team24.git ~/egg_inspector
bash ~/egg_inspector/deploy/install.sh --backend webcam      # or --backend pi on the finished station
```

The installer sets up everything the app needs to run **without internet**:

- system packages and the Python packages, PyTorch included;
- the crack model, which comes with the repository;
- **Ollama with the offline SLM.** It picks the size for the Pi's memory: `qwen2.5:1.5b` with 6 GB or more,
  `qwen2.5:0.5b` with 3 GB or more, none below (the fixed wording is used then);
- the desktop icon.

It ends with a self-check that runs the crack model and the SLM locally and prints **"Ready to run offline."**

**To update later, use `bash ~/egg_inspector/deploy/update.sh` instead of `git pull`.** It pulls from GitHub and
re-runs the installer with the same options, so new packages or SLM changes are installed too. A plain `git pull`
only fetches the files and installs nothing. Git never runs scripts from a repository by itself, for safety.

Check offline readiness any time:
`cd ~ && ~/.local/share/egg_inspector/venv/bin/python -m egg_inspector.selfcheck`.

The numbered steps below do the same by hand, plus the tests and the rig checks.

1. **Get the code and the model.** The model (`model_best_quantized.pth`, 88 MB) is in the repository, so this brings it
   too. The folder must be named `egg_inspector`, because the icon runs the app from there:
   ```bash
   git clone https://github.com/MCMagboo/PD_EggDetection_UI_Team24.git ~/egg_inspector   # first time
   bash ~/egg_inspector/deploy/update.sh                                                  # later updates
   ```
2. **Install the packages, PyTorch included.** The PyTorch download is large and takes a while:
   ```bash
   sudo apt update
   sudo apt install -y python3-venv python3-tk python3-pil.imagetk python3-numpy python3-opencv v4l-utils
   python3 -m venv --system-site-packages ~/egg-venv      # skip if it already exists
   mkdir -p ~/egg-tmp && TMPDIR=~/egg-tmp ~/egg-venv/bin/pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu   # CPU-only build
   ~/egg-venv/bin/pip install -r ~/egg_inspector/deploy/requirements.txt
   curl -fsSL https://ollama.com/install.sh | sh          # local SLM server for the heatmap explanation
   ollama pull qwen2.5:1.5b                                # the SLM itself, about 1 GB, once
   ```
   The desktop installer (steps 5 and 8) does the Ollama and SLM part by itself. See *Explainable AI*.
3. **Time the model on the Pi.** Copy one cracked egg photo to the Pi, e.g. `IMG_2480.jpg` to `~/` (from the PC:
   `scp "Duck egg dataset\Cracked\IMG_2480.jpg" <user>@<pi-ip>:~/`, or a USB stick), then:
   ```bash
   cd ~
   ~/egg-venv/bin/python -m egg_inspector.model_client ~/IMG_2480.jpg --heatmap heat.png
   ```
   It should print `CRACK  score 0.959  (… s, local model)`. The seconds are the Pi's time per photo; write them
   in *Test results*. Each egg takes about that time plus the swap countdown. If that's too slow, use the model
   API (`--model api`, see *The crack-detection model*).
4. **Live test with the USB webcam.** Plug the webcam in, find its path, and start the app:
   ```bash
   v4l2-ctl --list-devices                     # the webcam's block lists /dev/videoN; use the first one
   cd ~
   ~/egg-venv/bin/python -m egg_inspector --backend webcam --camera /dev/videoN
   ```
   The header must say **"Test mode: webcam …, local model"**. Tap **Live** next to Exit and hold an egg in front
   of the webcam. The result card and the XGrad-CAM panel update after every check. Tap **Live** again to stop,
   and **Exit** to close. See *Testing with a USB webcam* for more.
5. **Desktop icon for the webcam test** (optional). The installer finds the USB webcam by itself:
   ```bash
   cd ~/egg_inspector
   bash deploy/install.sh --backend webcam
   ```
   Then double-tap **Egg Inspector** on the desktop. If the webcam's number changes (another USB port, a
   reboot with different devices), run this again.

**On the station**, once the candling light, LEDs, buzzer and UV-C relay are wired:

6. **Check the wiring settings before connecting the UV-C lamp:** `PINS` and `ACTIVE_HIGH` in `config.py` must
   match the schematic and relay boards (see *Testing the hardware on the Pi*).
7. **Run one full tray from the terminal:**
   ```bash
   cd ~
   ~/egg-venv/bin/python -m egg_inspector --backend pi
   ```
8. **Install the desktop app for the station** once step 7 works. This replaces the webcam icon:
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
| `--backend webcam` | USB webcam + Live test, real model, no GPIO. Finds the webcam itself, or add `--camera /dev/videoN` |
| `--backend pi` | The station: real hardware + the trained model (needs `model_best_quantized.pth` + PyTorch, see *The crack-detection model*) |
| `--autostart` | Open the app automatically at login (kiosk mode) |
| `--no-apt` | Skip `apt-get` (offline or already installed) |
| `--no-slm` | Don't install Ollama and the SLM; heatmap explanations then use fixed wording |
| `--uninstall` | Remove icons, autostart entry and venv; keeps the source folder |

The installer installs the system packages, creates a venv at `~/.local/share/egg_inspector/venv`
(with access to the apt-installed `picamera2`/`gpiozero`), and adds the desktop and menu icons.

- **Logs:** `~/.local/state/egg_inspector/app.log`. If the app crashes, an error box shows the last lines.
- **Single instance:** a second tap on the icon while the app is open does nothing.
- **Exit:** the **Exit** button (top right) or Esc. Exit is disabled during an inspection or UV-C run.
- **Font:** the installer adds the Inter font if apt has it; otherwise the UI uses DejaVu Sans.

## Test results

Run these in order: first on a PC, then on the Pi. Fill in the **Pi** column as you go. Each test needs only
the ones above it.

- **PC** = Windows laptop, Intel 12th gen, 16 threads, PyTorch 2.14 CPU. Tested 2026-10-23 with
  `model_best_quantized.pth`.
- **Pi** = Raspberry Pi 5 **8 GB**, 32 GB SD card, USB webcam, Waveshare 7" (B). The installer picks `qwen2.5:1.5b`; no extra swap needed. *Not tested yet.*

| # | Test | Expected | PC | Pi |
|---|---|---|---|---|
| 1 | Model loads | No error | ✅ 2.3 s | |
| 2 | One cracked photo (`IMG_2480.jpg`) | `CRACK`, score ~0.96, heatmap saved | ✅ 0.959, 8.8 s | |
| 3 | One intact-looking photo (`IMG_2491.jpg`) | `no crack` | ✅ 0.130, 4.1 s | |
| 4 | Photo batch in the app (10 photos from `Cracked`) | 10 results; header says "local model" | ✅ 5 cracked, 6.2 s per egg | |
| 5 | Webcam opens | Live feed in the Optical panel | ✅ 640x480 | |
| 6 | Live test | Result card updates after every check | ✅ 4–7 s per check | |
| 7 | Live off/on quickly, Reset, Exit | One checking loop left; Reset stops Live; clean exit | ✅ | |
| 8 | Desktop icon in webcam mode | Header "Test mode: webcam …", Live button | PC has no icon | |
| 9 | Station (`--backend pi`) | Camera, candling light, LEDs, UV-C | needs the station | |
| 10 | Plain-language text beside the heatmap: rejected egg | SLM text, e.g. "…supporting the detection. The egg should be removed as cracked."; tap → measured facts + "written by qwen2.5:1.5b (local SLM)" | ✅ 0.8 s (first one after start ~5 s) | |
| 10b | Text beside the heatmap: accepted egg | "No crack found … so it passes." | ✅ | |
| 11 | Explanation without Ollama | Same facts in fixed wording, says Ollama isn't running | ✅ 2.5 s | |
| 12 | Candling: **Auto** on the Optical panel with a candled egg | Egg shell detail visible, not a white blob | laptop camera refuses exposure; Auto falls back to brightness, burnt-white area 2.5 % → 0 % (room scene, not yet a candled egg) | |
| 13 | Too bright: Start waits, Live doesn't guess | Amber "Too bright … Waiting", camera shown, carries on when usable; Stop works | ✅ with a pretend over-bright camera | |
| 14 | Offline (Wi-Fi off): photo test + tap a heatmap | Same results and SLM text | not yet tried | |
| 15 | Automated laptop test (20 checks: model, inspection logic, simulated GPIO, app, webcam) | All pass | ✅ 19/20: only the Modal API failed (server down/slow, not used offline) | |

Speeds are per photo. A crack takes longer, because the heatmap needs an extra step.

### Commands

On the PC (PowerShell), from the folder that contains `egg_inspector\`:

```powershell
python -m egg_inspector.model_client "Duck egg dataset\Cracked\IMG_2480.jpg" --heatmap heat.png      # tests 1-2
python -m egg_inspector.model_client "Duck egg dataset\Cracked\IMG_2491.jpg"                         # test 3
python -m egg_inspector --backend images --images "Duck egg dataset\Cracked" --windowed              # test 4: press Start
python -m egg_inspector --backend webcam --camera 0 --windowed                                       # tests 5-7: tap Live
```

On the Pi (bash). The dataset photos aren't in the repository, so copy one cracked photo over first, for
example to `~/IMG_2480.jpg`, or use a test photo from the station camera:

The same steps as *Deploy on the Raspberry Pi* 3–5:

```bash
bash ~/egg_inspector/deploy/update.sh && cd ~
~/egg-venv/bin/python -m egg_inspector.model_client ~/IMG_2480.jpg --heatmap heat.png              # tests 1-2
v4l2-ctl --list-devices                                                                              # find the webcam
~/egg-venv/bin/python -m egg_inspector --backend webcam --camera /dev/videoN                         # tests 5-7
cd ~/egg_inspector && bash deploy/install.sh --backend webcam                                        # test 8
```

The model check prints its time, for example `CRACK  score 0.959  (8.8 s, local model)`. Write that number in
the Pi column. In the app, the time is shown under the verdict (`… · 4.6 s`).

## Operating the station

| Control | What it does |
|---|---|
| **Start** | Inspects all 10 eggs, then runs UV-C if cracks were found (see below) |
| **Stop** | Stops inspection or UV-C immediately |
| **Reset** | Clears the screen and the defect LEDs |
| **Tray eggs** | Tap an inspected egg to review its images and result. The egg being inspected is outlined in blue |
| **Optical / XGrad-CAM image** | Tap to see it full size (the images are small on a 7" screen). Tap again to close. Start/Stop/Reset stay visible |
| **Text beside the heatmap** | What the result means in plain words: why the egg was rejected or passed, and what to do (see *Explainable AI*) |
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
| `hwtest.py` | Hardware bring-up menu: test the candling light, LEDs, buzzer, door switch, UV-C and camera on the Pi |
| `ui.py` | CustomTkinter touchscreen UI (main thread only) |
| `controller.py` | Background worker: inspection cycle, UV-C policy and safety checks |
| `backend_base.py` | `Backend` interface that both backends implement |
| `mock_backend.py` | Fake images for development without hardware |
| `image_backend.py` | `--backend images` (photo folder) and `--backend webcam` (USB webcam, Live test): the real model, no hardware |
| `camera.py` | Webcam and Pi camera at a fixed frame rate (reader thread), light control, and `capture()`: the sharpest usable frame of half a second |
| `pi_backend.py` | Real camera, GPIO, UV-C relay; one picture per egg goes to the model. Also `NoModelPiBackend` (real camera + hardware, simulated verdicts) and `FakeVisionPiBackend` (no camera) |
| `local_model.py` | Runs `model_best_quantized.pth` (Mask R-CNN + XGrad-CAM) on this machine; the default |
| `explain.py` | Measures the heatmap and has the local SLM (Ollama) explain it; fixed-wording fallback |
| `test_explain.py` | Checks for `explain.py`: `python -m egg_inspector.test_explain` |
| `selfcheck.py` | Offline readiness check (crack model, PyTorch, Ollama + SLM, with timings): `python -m egg_inspector.selfcheck` |
| `model_best_quantized.pth` | The trained model weights (88 MB, half precision), used by the local model |
| `model_client.py` | Client for the model API, `load_model()` (picks local or API), and a one-photo command-line check |
| `api_contract.py` | The API's response format, copied unchanged from the model team's client |
| `datatypes.py` | `EggResult`, `Summary`, `UvcOutcome`, `Event` |
| `deploy/` | Pi installer (`install.sh`, also installs the offline SLM), `update.sh` (pull + reinstall), launcher, icon, pip requirements |

The UI never talks to hardware directly. The controller runs on a worker thread, calls the backend, and
sends events to the UI through a queue. You can swap the mock for the real backend without UI changes.

## How each egg is inspected

**No motors for now.** The camera is fixed and the operator places each egg in front of it; the 10 eggs on the
screen are 10 slots. The tray and spin motor code (tray homing, finding eggs with the camera, filming the egg
while it turns) is in git history at commit `9752eeb`, for when the mechanism is ready.

1. **Light on:** the candling light (`backlight` pin) switches on.
2. **Swap:** before each egg after the first, the status line counts down `swap_seconds` (5 s): "Place egg 2/10
   in front of the camera". Put the next egg in then.
3. **Picture:** the app waits while the picture is burnt white ("Too bright", see *Testing with a USB webcam*),
   then takes the **sharpest** frame of half a second at the camera's fixed frame rate.
4. **Model:** Mask R-CNN scores it. A crack shows the XGrad-CAM heatmap and the explanation beside it.
5. **Reject:** a cracked egg lights its defect LED and beeps.
6. **UV-C:** after the last egg, UV-C runs if a crack was found (door closed). The candling light is **off
   during UV-C**, and when the batch ends or is stopped. **Live** switches it on while Live runs.

### Wiring (no motors)

Physical pin numbers on the Pi header (pin 1 is the corner pin next to the "J8" label). `pinout` on the Pi prints
the map.

| Part | Connect | Pi pin |
|---|---|---|
| Candling light: MR16 through a relay module (e.g. Keyes SR1y) | relay **S** / **+** / **−** | **29** / **2** (5V) / **9** (GND) |
| | relay **COM** → 12 V supply +, relay **NO** → MR16, MR16 other wire → 12 V supply − | – |
| Defect LEDs 1–10 | pin → 330 Ω → LED long leg; short legs → GND rail → Pi GND (pin **14**) | **3, 5, 7, 8, 10, 11, 12, 15, 16, 18** |
| Buzzer (optional) | + to the pin, − to GND | **35** |
| UV-C relay (optional) | relay module **IN** | **31** |
| Door switch (optional) | between the pin and GND, closed when the door is shut | **37** |

- **Relay polarity:** the Keyes SR1y switches on when S is HIGH, which matches `ACTIVE_HIGH` in `config.py`.
  For a module that switches on when its input is LOW, set that part to `False`. Check with `hwtest`
  (**Backlight on/off**) before connecting the UV-C lamp.
- **LEDs 4 and 5** use the serial pins: `sudo raspi-config` → *Interface Options* → *Serial Port* → **No** to the
  login shell.
- **Without a door switch** the app reads the door as open, so UV-C never starts. Everything else works.
- **Never** put 5 V or 12 V on a GPIO pin; they take 3.3 V only.

## Testing with a USB webcam (Live test)

`--backend webcam` runs the normal app with a USB webcam and no GPIO: LEDs, buzzer and UV-C are only logged.

- **Live** (button next to Exit): the Optical panel follows the camera, and the model checks the newest frame
  non-stop. The result card and the XGrad-CAM panel update after each check: about 5 s on a laptop, longer on
  a Pi CPU. Tap **Live** again to stop.
- **Start:** the normal 10-slot cycle. Swap the egg during the countdown before each slot; each slot takes the
  sharpest usable frame of half a second.
  Pressing Start switches Live off.
- **Light** (− ☀ + Auto, on the Optical panel): sets the webcam's exposure for candling. A candled egg is lit
  from behind and is far brighter than a room. The webcam's automatic exposure therefore turns it into a white
  "sun", and the model sees no crack. Hold a candled egg in front of the camera and tap **Auto**. The app tries
  each exposure from the darkest up and keeps the brightest one where at most 1 % of the picture is burnt white.
  Then use **−** / **+** to fine-tune. The setting is saved in `~/.config/egg_inspector/camera.json` and used
  next time.
  - Some webcams (many built-in laptop cameras) don't let apps set the exposure. **Auto** then turns the
    brightness down instead (−/+ show steps out of 7). On the test laptop this cut the burnt-white area from 2.5 %
    to 0 %. If the egg still glows white at the darkest step, the light is too strong for that camera.
  - **Too bright = wait, never guess.** If more than 2 % of the picture is burnt white, the app doesn't judge the
    egg. In **Start** it waits on that egg, shows the camera in the Optical panel and an amber "Too bright … Waiting"
    message, and carries on as soon as the picture is usable. **Stop** still works. In **Live** the result card shows
    "Too bright to judge" instead of a verdict.
  - Also helps: a dark box around the egg and camera, and a dimmer or diffused candling light.

On the Pi (bash), after the *Deploy on the Raspberry Pi* steps 1–3:

```bash
sudo apt install -y python3-opencv v4l-utils     # the installer does this too with --backend pi
v4l2-ctl --list-devices                           # find the webcam's /dev/videoN
cd ~
~/egg-venv/bin/python -m egg_inspector --backend webcam --camera /dev/video0
```

Use the webcam's own path from `v4l2-ctl` (on a Pi 5 it isn't always `/dev/video0`). Add `--windowed` to run in
a window, or `--model api` to use the model API.

**Frame rate.** The app asks the webcam for a fixed **30 fps at 1280×720 in MJPG**. Most USB webcams only reach
30 fps at 720p in MJPG; the raw format drops to 5–10 fps. On Linux it also switches off the webcam's habit of
slowing down in dim light (`exposure_dynamic_framerate`). Light steps longer than one frame (1/30 s) are left out
of − / + and Auto, so a manual exposure can't slow the camera down either. The real rate shows during **Live**
("Camera 30 fps"). If it is much lower, check what the webcam can do:

```bash
v4l2-ctl -d /dev/video0 --list-formats-ext      # look for MJPG 1280x720 at 30 fps
```

and set `camera_size` (e.g. `(640, 480)`) or `--fps` to a mode the webcam lists. Our laptop's built-in camera has
no MJPG and gives 10 fps at 720p and 17 fps at 480p. That's still usable: about 5 frames per 45° slice.

**Sharpest frame:** Live and Start check the sharpest frame of the last half second, not simply the newest one,
so an egg that moves a little is judged on its least blurred frame.

**The station with a USB webcam:** `--backend pi --camera /dev/videoN` uses the webcam instead of the ribbon camera,
with the candling light, LEDs, buzzer and UV-C. The installer takes the same:
`bash deploy/install.sh --backend pi --camera /dev/videoN`.

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

**The app uses the full `model_best.pth` (176 MB) whenever it is in the `egg_inspector` folder**, and
`model_best_quantized.pth` otherwise (`model_path` / `model_fallback` in `config.py`). The full file is too big for
GitHub (the limit is 100 MB per file), so `.gitignore` keeps it out and it must be **copied in by hand** (PC
PowerShell, from the folder that contains `egg_inspector\`):

```powershell
scp egg_inspector\model_best.pth team24@team24.local:~/egg_inspector/
```

`selfcheck` prints which file it uses (`crack model file  model_best.pth (168 MB)`). Both files load as the same
32-bit network, so they use the same memory and time per photo. Keep later model updates rare: every new version
of the small file adds another 88 MB to the repository's history.

On the Pi (bash):

```bash
mkdir -p ~/egg-tmp && TMPDIR=~/egg-tmp ~/egg-venv/bin/pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu   # CPU-only build
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

### Explainable AI: the heatmap explained in words

The XGrad-CAM card shows the heatmap and, **right beside it, a plain-language reading of the result**:

- **Rejected egg:** where the crack is, whether the heatmap supports it, and what to do. For example: "The heatmap
  strongly indicates the presence of the crack, supporting the detection. The egg should be removed as cracked."
  A small language model (SLM) running on the same machine writes this text.
- **Accepted egg:** why it passed. For example: "No crack found. The model's highest crack score was 13%, below
  the 83% needed to reject the egg, so it passes."
- **Too bright to judge:** says the model is waiting instead of guessing (see *Testing with a USB webcam*).

The result card also shows a short summary, e.g. "Micro-fissure · heatmap centre, 90% on the crack". Tap the
heatmap for the big view: the full text, the measured facts, and who wrote the text.

How it stays faithful (`explain.py`):

1. **Measure:** code measures the heatmap: where it is strongest, how much of the photo is hot, how many separate
   spots it has, and how much of it lies inside the box of the detected crack.
2. **Decide:** code turns that last number into the conclusion and action:
   - 60 % or more on the crack: **strong**. The model looked at the crack, so remove the egg.
   - 30–60 %: **partial**. Remove the egg, and also look at the other hot area.
   - under 30 %: **weak**. The model probably reacted to dirt, a shadow or the background, so check the egg by
     hand.
3. **Word:** the SLM (`qwen2.5:1.5b` in Ollama, offline) rewrites those facts as 2–3 plain sentences. It never
   sees the image and doesn't decide anything.
4. **Check:** if its answer contradicts the decided action, the app shows a fixed-wording explanation instead.
   It does the same when Ollama isn't installed or running. The screen always lists the measured facts and says
   who wrote the text.

The code's own wording appears at once. The SLM's version replaces it a moment later. The SLM runs in the
background, one explanation at a time, so it never holds up an inspection. The app loads it at start. On the
laptop an explanation takes under 1 s once loaded. Expect several seconds on a Pi 5.

Install the SLM once per machine. It is too big for GitHub, so the installer downloads it (about 1 GB):

- **Pi:** `bash deploy/install.sh --backend …` does it, or by hand:
  `curl -fsSL https://ollama.com/install.sh | sh`, then `ollama pull qwen2.5:1.5b`.
- **Windows (PowerShell):** `winget install Ollama.Ollama`, then open a new PowerShell and run
  `ollama pull qwen2.5:1.5b`.

Checks: `python -m egg_inspector.test_explain`.

### Offline, storage and memory

**Offline:** after the one-time install, everything runs on the machine with no internet:

- the crack model is the file in the folder, and nothing is downloaded at run time;
- the SLM is reached only at `127.0.0.1`, which is the machine itself.

Only `--model api` uses the internet. Internet is needed once, to install: `git clone`, the Python packages, and
Ollama with its SLM.

Measured on the test laptop:

| Part | Disk | Memory (RAM) while in use |
|---|---|---|
| App + crack model (`model_best_quantized.pth`) | ~85 MB (+ ~80 MB git history) | 540 MB loaded, ~1.6 GB peak while making a heatmap |
| PyTorch + torchvision | ~550 MB | (included above) |
| OpenCV, other Python packages | ~180 MB | small |
| SLM `qwen2.5:1.5b` | 941 MB | ~1.2 GB, kept loaded 30 min after use |
| Ollama program | 3.1 GB on Windows (mostly graphics drivers; smaller on the Pi) | — |
| Raspberry Pi OS desktop | — | ~0.6–0.8 GB |

Worst moment with the 1.5B SLM: about **3.5 GB of RAM**. The installer picks the SLM by the Pi's memory, and
the app uses whichever one is installed (`free -h` shows the memory):

- **6 GB or more (8 GB Pi):** `qwen2.5:1.5b`, the best wording.
- **3–6 GB (4 GB Pi):** `qwen2.5:0.5b` (~400 MB disk, ~0.5 GB RAM). The wording is weaker, but the guard still
  checks the action.
- **Under 3 GB (2 GB Pi):** no SLM. The fixed wording uses no extra memory and gives the same facts and conclusion.

Disk: plan on 2–3 GB in total on the Pi with the 1.5B SLM, or under 1 GB without it (`df -h /` shows free space).

### Settings and timing (`config.py`)

| Setting | Meaning |
|---|---|
| `model_source` | `"local"` (default) or `"api"` |
| `model_path` | Local weights file; relative paths are inside the `egg_inspector` folder |
| `detect_threshold` | `None` uses the model's validated threshold (0.83). A number from 0.01 to 0.99 overrides it. |
| `model_api_url` | Base URL of the deployed API (without `/predict`) |
| `swap_seconds` | Countdown before each egg after the first, to place it in front of the camera (5 s) |
| `camera_fps`, `camera_size` | Fixed camera frame rate (30) and USB webcam resolution (1280×720) |
| `explain_model` | SLM for the heatmap explanation (`qwen2.5:1.5b`). `qwen2.5:0.5b` is faster but often drops the conclusion |
| `explain_url` | Ollama on this machine (`http://127.0.0.1:11434`) |

- **Speed on the Pi (local):** not measured yet. A Pi 5 CPU is several times slower than a laptop, so expect
  a few seconds per egg (selfcheck prints it). Measure one tray. If it's too slow, use `--model api` on the Pi.
- **Network:** only the API needs internet. If it can't be reached, the batch stops with an error on screen.
- **Camera settings:** the model should see photos like the ones it was trained on: same focus, backlight,
  exposure and white balance. Share some photos from the station's IMX219 with the model team.

### Known open items

- The "too bright" limit (2 % burnt white) and the Light **Auto** were tuned with a pretend camera and a room scene.
  Check them with real candled eggs on the station's webcam.
- Offline use is confirmed from the code (only `127.0.0.1` and local files), and the self-check passes on the
  laptop. It hasn't yet been tried with Wi-Fi switched off on the Pi (test 14).
- The model team's Modal API is often slow (minutes) or drops connections. The local model doesn't use it.

- Camera exposure, gain and white balance are automatic. Once the backlight and focus are final, lock them to
  fixed values so every egg is photographed the same way. This isn't built yet; the plan is new settings in
  `config.py` applied in `PiBackend._init_camera`.

- **Motors are removed for now:** the operator places each egg. The tray and spin code (incl. finding eggs with the
  camera and filming the egg while it turns) is at commit `9752eeb`; bring it back with git when the mechanism
  is ready.
- `eggs_loaded()` always returns `True`; it needs a tray-presence sensor or camera check.
- GPIO pin numbers in `config.py` are placeholders; match them to the schematic.
- `ACTIVE_HIGH` in `config.py` must match each relay board (see *Testing the hardware on the Pi*).
