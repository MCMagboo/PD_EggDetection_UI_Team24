"""Touchscreen UI (CustomTkinter), laid out after Figure 2-11 of the design paper.

Screen: optical | XGrad-CAM heatmap (Mask R-CNN), result + confidence, UV-C panel
(countdown ring, auto-on-cracks switch, manual Run UV-C with tap-again confirm),
10-egg tray strip (tap an egg to review it), and big Start / Stop / Reset buttons.
Widgets are only touched from the main thread; the worker thread talks through a queue.

Round shapes (tray eggs, UV-C ring, badge, logo, image corners) are drawn with Pillow at
SS x size and scaled down, because Tk canvases are not anti-aliased on the Pi (X11).
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import tkinter.font as tkfont
from functools import lru_cache
from math import cos, radians, sin

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

from .backend_base import Backend
from .config import Config
from .controller import InspectionController
from .datatypes import EggResult, Event, Summary, UvcOutcome
from .explain import Explainer, fact_lines, interpret, summary
from .local_model import VALIDATED_THRESHOLD
from .camera import TOO_BRIGHT, burnt, sweep

C = {
    "bg": "#EEF2F7", "card": "#FFFFFF", "subtle": "#F3F6FA", "line": "#E3E8EF", "track": "#E3E8EF",
    "disabled": "#E4E9F0", "well": "#111827",
    "text": "#0F172A", "muted": "#64748B", "faint": "#A0AEC0",
    "blue": "#2563EB", "green": "#16A34A", "red": "#DC2626", "amber": "#D97706", "uv": "#7C3AED",
    "cream": "#F3E3C3",
}
BUTTONS = {                               # kind -> (fill, text colour)
    "primary": (C["blue"], "#FFFFFF"), "danger": (C["red"], "#FFFFFF"), "uv": (C["uv"], "#FFFFFF"),
    "warn": (C["amber"], "#FFFFFF"), "secondary": (C["card"], C["text"]),
}
TRAY_COLORS = {"ok": C["green"], "bad": C["red"]}
VERDICTS = {"idle": ("Waiting", C["faint"]), "ok": ("Accepted", C["green"]), "bad": ("Rejected", C["red"])}
LIVE_FEED_MS = 50                         # live picture refresh on screen (20 fps; the camera itself runs at camera_fps)
LIVE_WINDOW_S = 0.5                       # Live checks the sharpest frame of the last half second (a turning egg blurs)
UVC_CONFIRM_MS = 3000                     # second tap must land within this window
SS = 3                                    # supersampling factor for Pillow-drawn shapes
TK_FAMILIES = ("Inter", "Segoe UI", "DejaVu Sans")      # first one installed wins
PIL_FALLBACK = {False: ("DejaVuSans.ttf", "segoeui.ttf", "arial.ttf"),
                True: ("DejaVuSans-Bold.ttf", "segoeuib.ttf", "arialbd.ttf")}
_family = "DejaVu Sans"                   # set by App once Tk can list the installed fonts


# ------------------------------------------------------------------ helpers
def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _mix(a: str, b: str, t: float) -> str:
    """`t` parts of colour a over (1 - t) parts of b."""
    return "#%02x%02x%02x" % tuple(round(x * t + y * (1 - t)) for x, y in zip(_rgb(a), _rgb(b)))


def _darker(color: str, f: float = 0.88) -> str:
    return _mix(color, "#000000", f)


def _tint(color: str) -> str:
    """Pastel background for badges and banners of a given accent colour."""
    return _mix(color, C["card"], 0.12)


def _font_file(bold: bool) -> str | None:
    """Font file matching the UI family, so Pillow-drawn text looks like the Tk text."""
    if _family == "Segoe UI":
        return "segoeuib.ttf" if bold else "segoeui.ttf"
    if sys.platform.startswith("linux"):
        try:
            out = subprocess.run(["fc-match", "-f", "%{file}", f"{_family}:{'bold' if bold else 'regular'}"],
                                 capture_output=True, text=True, timeout=2).stdout.strip()
            return out or None
        except (OSError, subprocess.SubprocessError):
            pass
    return None


@lru_cache(maxsize=None)
def _pil_font(px: int, bold: bool = False) -> ImageFont.ImageFont:
    for name in (_font_file(bold), *PIL_FALLBACK[bold]):
        if not name:
            continue
        try:
            font = ImageFont.truetype(name, px)
        except OSError:
            continue
        if bold:
            try:
                font.set_variation_by_name("Bold")   # variable fonts (e.g. InterVariable) ship one file
            except Exception:
                pass
        return font
    try:
        return ImageFont.load_default(px)
    except TypeError:                            # Pillow < 10.1 (Raspberry Pi OS Bookworm ships 9.4)
        return ImageFont.load_default()


def _downsample(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return img.resize(size, Image.LANCZOS)


def _round_caps(d: ImageDraw.ImageDraw, pts, w: float, color: str) -> None:
    for x, y in pts:
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=color)


def _logo(px: int, bg: str) -> Image.Image:
    s = px * SS
    img = Image.new("RGB", (s, s), bg)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), s * 0.30, fill=C["uv"])
    ew, eh = s * 0.42, s * 0.56
    d.ellipse((s / 2 - ew / 2, s / 2 - eh / 2 + s * 0.02, s / 2 + ew / 2, s / 2 + eh / 2 + s * 0.02), fill=C["cream"])
    crack = [(x * s, y * s) for x, y in [(0.47, 0.30), (0.54, 0.44), (0.45, 0.53), (0.55, 0.66)]]
    w = max(2, s // 16)
    d.line(crack, fill=C["red"], width=w, joint="curve")
    _round_caps(d, crack, w, C["red"])
    return _downsample(img, (px, px))


def _badge(px: int, state: str) -> Image.Image:
    """Round verdict badge: pastel disc with a check, cross or dash drawn as strokes."""
    color = VERDICTS[state][1]
    s = px * SS
    img = Image.new("RGB", (s, s), C["card"])
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, s - 1, s - 1), fill=_tint(color) if state != "idle" else C["subtle"])
    strokes = {"ok": [[(0.31, 0.52), (0.44, 0.65), (0.69, 0.38)]],
               "bad": [[(0.37, 0.37), (0.63, 0.63)], [(0.63, 0.37), (0.37, 0.63)]],
               "idle": [[(0.38, 0.50), (0.62, 0.50)]]}[state]
    w = max(2 * SS, s // 14)
    for line in strokes:
        pts = [(x * s, y * s) for x, y in line]
        d.line(pts, fill=color, width=w, joint="curve")
        _round_caps(d, pts, w, color)
    return _downsample(img, (px, px))


# --------------------------------------------------------------- components
class ImagePanel(ctk.CTkFrame):
    """One image card. Tapping it calls on_tap(image, title) so the app can show the image full size."""

    def __init__(self, master, title, hint, accent, size, fonts, radius, corner, on_tap=None, text_width=0):
        super().__init__(master, fg_color=C["card"], border_color=C["line"], border_width=1, corner_radius=corner)
        full = size
        size = (size[0] - text_width, size[1])                      # with text_width, the text takes the right side
        self.size, self.title, self.on_tap = size, title, on_tap
        self.source: Image.Image | None = None             # full-resolution image behind the thumbnail
        mask = Image.new("L", (size[0] * SS, size[1] * SS), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] * SS - 1, size[1] * SS - 1), radius * SS, fill=255)
        self._mask = _downsample(mask, size)
        self._blank = ctk.CTkImage(self._rounded(Image.new("RGB", size, C["subtle"])), size=size)
        self._img = None
        head = self.head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(8, 6))
        ctk.CTkLabel(head, text="●", text_color=accent, font=fonts["dot"], height=0).pack(side="left")
        ctk.CTkLabel(head, text=title, text_color=C["text"], font=fonts["title"], height=0).pack(side="left", padx=(6, 0))
        if hint:
            ctk.CTkLabel(head, text=hint, text_color=C["faint"], font=fonts["hint"], height=0).pack(side="left", padx=(6, 0))
        self.zoom_hint = ctk.CTkLabel(head, text="", text_color=C["faint"], font=fonts["hint"], height=0)
        self.zoom_hint.pack(side="right")
        body = ctk.CTkFrame(self, fg_color="transparent", width=full[0], height=full[1])
        body.pack(padx=8, pady=(0, 8))
        body.pack_propagate(False)                                   # long text never makes the card taller
        self.view = ctk.CTkLabel(body, text="", image=self._blank, width=size[0], height=size[1],
                                 fg_color=C["card"], text_color=C["faint"], font=fonts["hint"], corner_radius=0)
        self.view.pack(side="left")
        self.text = None
        if text_width:
            self.text = ctk.CTkLabel(body, text="", text_color=C["text"], font=fonts["text"], justify="left",
                                     anchor="nw", wraplength=text_width - 10)
            self.text.pack(side="left", fill="both", expand=True, padx=(10, 0))
        for w in (self, head, body, self.view, self.zoom_hint, *([self.text] if self.text else [])):
            w.bind("<Button-1>", self._tapped)                       # the whole card is the touch target

    def _tapped(self, _event=None) -> None:
        if self.source is not None and self.on_tap:
            self.on_tap(self.source, self.title)

    def _rounded(self, img: Image.Image) -> Image.Image:
        out = Image.new("RGB", self.size, C["card"])
        # fit, don't stretch: a 4:3 camera frame in a wide panel gets dark side bars, and the egg keeps its shape
        fitted = ImageOps.pad(img.convert("RGB"), self.size, Image.LANCZOS, color=(0, 0, 0))   # sharpest downscale
        out.paste(fitted, (0, 0), self._mask)
        return out

    def say(self, text: str) -> None:
        if self.text is not None:
            self.text.configure(text=text if len(text) <= 260 else text[:250].rsplit(" ", 1)[0] + "… (tap for more)")

    def show(self, img: Image.Image | None, empty_text: str = "No image") -> None:
        self.source = img
        self.zoom_hint.configure(text="⤢  Tap to enlarge" if img is not None else "")
        if img is None:
            self._img = None
            self.view.configure(image=self._blank, text=empty_text)   # never image=None (CTk bug)
            return
        self._img = ctk.CTkImage(self._rounded(img), size=self.size)
        self.view.configure(image=self._img, text="")


class ZoomView(ctk.CTkFrame):
    """Full-size view of one image over the top of the screen; tap anywhere on it to close.
    It stops above the Start/Stop/Reset row, so Stop is always reachable.
    With `text`, the image takes the left side and the text (the heatmap explanation) the right."""

    def __init__(self, master, img: Image.Image, title: str, size: tuple[int, int], fonts, text: str | None = None):
        super().__init__(master, fg_color=C["well"], corner_radius=0, width=size[0], height=size[1])
        self.pack_propagate(False)                     # keep the given size whatever the image is
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(head, text=title, text_color="#FFFFFF", font=fonts["title"], height=0).pack(side="left")
        ctk.CTkLabel(head, text="Tap to close", text_color=C["faint"], font=fonts["hint"], height=0).pack(side="right")
        head.update_idletasks()
        text_w = int(size[0] * 0.48) if text is not None else 0
        box = (size[0] - 16 - text_w, max(1, size[1] - head.winfo_reqheight() - 20))
        fitted = ImageOps.contain(img.convert("RGB"), box, Image.LANCZOS)
        self._img = ctk.CTkImage(fitted, size=fitted.size)
        view = ctk.CTkLabel(self, text="", image=self._img, fg_color=C["well"])
        view.pack(side="left" if text is not None else "top", expand=True)
        self.text = None
        if text is not None:
            self.text = ctk.CTkLabel(self, text=text, text_color="#E5E7EB", font=fonts["hint"], justify="left",
                                     anchor="nw", wraplength=text_w - 24, fg_color=C["well"])
            self.text.pack(side="right", fill="both", expand=True, padx=(4, 16), pady=(4, 8))
        for w in (self, head, view, *head.winfo_children(), *([self.text] if self.text else [])):
            w.bind("<Button-1>", lambda e: self.destroy())

    def set_text(self, text: str) -> None:
        if self.text is not None and self.winfo_exists():
            self.text.configure(text=text)


class TrayStrip(ctk.CTkCanvas):
    """Ten egg-shaped slots. States: pending | active (being inspected) | ok | bad."""

    def __init__(self, master, count, height, font_px, on_pick):
        super().__init__(master, height=height, bg=C["card"], highlightthickness=0)
        self.count, self.on_pick = count, on_pick
        self.font = _pil_font(font_px * SS, bold=True)
        self.states = ["pending"] * count
        self.selected: int | None = None
        self._photo = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._click)

    def set_state(self, i: int, state: str) -> None:
        self.states[i] = state
        self.redraw()

    def select(self, i: int | None) -> None:
        self.selected = i
        self.redraw()

    def reset(self) -> None:
        self.states, self.selected = ["pending"] * self.count, None
        self.redraw()

    def clear_active(self) -> None:
        self.states = ["pending" if s == "active" else s for s in self.states]
        self.redraw()

    def redraw(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 10:
            return
        img = Image.new("RGB", (w * SS, h * SS), C["card"])
        d = ImageDraw.Draw(img)
        cell = w * SS / self.count
        ry = h * SS / 2 - 5 * SS
        rx = min(ry * 0.78, cell / 2 - 6 * SS)
        ring = 4 * SS
        for i, state in enumerate(self.states):
            cx, cy = cell * (i + 0.5), h * SS / 2
            box = (cx - rx, cy - ry, cx + rx, cy + ry)
            if i == self.selected:
                color = TRAY_COLORS.get(state, C["blue"])
                d.ellipse((box[0] - ring, box[1] - ring, box[2] + ring, box[3] + ring),
                          outline=_mix(color, C["card"], 0.45), width=3 * SS)
            if state == "pending":
                d.ellipse(box, fill=C["subtle"], outline=C["line"], width=2 * SS)
                num = C["faint"]
            elif state == "active":
                d.ellipse(box, fill=_tint(C["blue"]), outline=C["blue"], width=2 * SS)
                num = C["blue"]
            else:
                d.ellipse(box, fill=TRAY_COLORS[state])
                num = "#FFFFFF"
            d.text((cx, cy), str(i + 1), fill=num, font=self.font, anchor="mm")
        self._photo = ImageTk.PhotoImage(_downsample(img, (w, h)))
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)

    def _click(self, event) -> None:
        i = int(event.x // (max(self.winfo_width(), 1) / self.count))
        if 0 <= i < self.count and self.states[i] in TRAY_COLORS:
            self.on_pick(i)


class UvcRing(ctk.CTkCanvas):
    """Circular UV-C countdown: elapsed seconds in the middle, progress arc around it."""

    def __init__(self, master, diameter):
        super().__init__(master, width=diameter, height=diameter, bg=C["card"], highlightthickness=0)
        self.d = diameter
        self.big = _pil_font(int(diameter * 0.30) * SS, bold=True)
        self.small = _pil_font(int(diameter * 0.12) * SS)
        self._photo = None

    def set(self, elapsed: int, total: int, color: str) -> None:
        s = self.d * SS
        w = max(3 * SS, s // 12)
        img = Image.new("RGB", (s, s), C["card"])
        d = ImageDraw.Draw(img)
        box = (0, 0, s - 1, s - 1)
        d.ellipse(box, outline=C["track"], width=w)
        frac = max(0.0, min(1.0, elapsed / total))
        if frac > 0:
            d.arc(box, -90, -90 + 360 * frac, fill=color, width=w)
            r, c = s / 2 - w / 2, s / 2
            _round_caps(d, [(c + r * cos(radians(a)), c + r * sin(radians(a))) for a in (-90, -90 + 360 * frac)],
                        w, color)
        d.text((s / 2, s * 0.46), str(elapsed), fill=C["text"], font=self.big, anchor="mm")
        d.text((s / 2, s * 0.68), f"of {total} s", fill=C["muted"], font=self.small, anchor="mm")
        self._photo = ImageTk.PhotoImage(_downsample(img, (self.d, self.d)))
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)


class Toggle(ctk.CTkCanvas):
    """iOS-style switch. CTkSwitch's knob is as tall as its track, so a white knob vanishes on white cards."""

    def __init__(self, master, variable: ctk.BooleanVar, command, size: tuple[int, int], on_color: str):
        super().__init__(master, width=size[0], height=size[1], bg=C["card"], highlightthickness=0)
        self.var, self.command, self.size, self.on_color = variable, command, size, on_color
        self._photo = None
        self.bind("<Button-1>", lambda e: self.toggle())
        variable.trace_add("write", lambda *_: self.redraw())
        self.redraw()

    def toggle(self) -> None:
        self.var.set(not self.var.get())
        self.command()

    def redraw(self) -> None:
        w, h = self.size[0] * SS, self.size[1] * SS
        on = bool(self.var.get())
        img = Image.new("RGB", (w, h), C["card"])
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, w - 1, h - 1), h / 2, fill=self.on_color if on else "#CBD5E1")
        m = 3 * SS
        k = h - 2 * m
        x = w - m - k if on else m
        d.ellipse((x, m, x + k, m + k), fill="#FFFFFF")
        self._photo = ImageTk.PhotoImage(_downsample(img, self.size))
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)


def pixel_exact() -> None:
    """Call before creating the window: 1 widget px = 1 screen px, so the layout math holds."""
    ctk.deactivate_automatic_dpi_awareness()
    if sys.platform == "win32":                    # ...but still tell Windows we draw our own pixels, or a laptop at
        try:                                       # 125 %/150 % stretches the whole window as a bitmap (blurry);
            import ctypes                          # the Pi is unaffected

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
    ctk.set_appearance_mode("light")


# ---------------------------------------------------------------------- app
class App(ctk.CTk):
    def __init__(self, cfg: Config, backend: Backend):
        global _family
        pixel_exact()
        super().__init__(fg_color=C["bg"])
        installed = set(tkfont.families(self))
        _family = next((f for f in TK_FAMILIES if f in installed), "TkDefaultFont")
        self.cfg, self.backend = cfg, backend
        self.events: queue.Queue = queue.Queue()
        self.ctrl = InspectionController(backend, cfg, self.events)
        self.results: dict[int, EggResult] = {}
        self.auto_uvc = ctk.BooleanVar(value=cfg.auto_uvc)
        self._fonts: dict[tuple[int, bool], ctk.CTkFont] = {}
        self._confirm_job: str | None = None
        self._uvc_elapsed = 0
        self._live = False
        self._live_stop = threading.Event()            # set when Live turns off: ends that session's checks
        self.cam = getattr(backend, "camera", None)    # the camera the UI can watch (webcam or Pi camera), or None
        self._shown: EggResult | None = None           # the result on screen (its heatmap can be explained)
        self.explainer = Explainer(cfg.explain_model, cfg.explain_url)
        self._slm_busy = False
        self._too_bright = False
        self._threshold = cfg.detect_threshold or VALIDATED_THRESHOLD
        threading.Thread(target=self.explainer.warm, daemon=True).start()
        self._live_run = 0                             # bumps each time Live turns on
        self.title("Duck Egg Inspector")
        self._setup_window()
        self.s = self.H / 480                          # scale factor so 1024x600 etc. also fit
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.bind("<Escape>", lambda e: self._quit())
        self._reset_view()
        self._set_running(False)
        self.after(40, self._poll)
        self._tick_clock()

    # ---------------------------------------------------------------- window
    def _setup_window(self) -> None:
        if self.cfg.fullscreen:
            self.attributes("-fullscreen", True)
            try:
                self.configure(cursor="none")          # hide the mouse pointer on a touchscreen
            except Exception:
                pass
            self.update_idletasks()
            self.W, self.H = self.winfo_screenwidth(), self.winfo_screenheight()
        else:
            self.W, self.H = self.cfg.screen_w, self.cfg.screen_h
            self.geometry(f"{self.W}x{self.H}")

    def _f(self, size: int, bold: bool = False) -> ctk.CTkFont:
        """Font sized in points (like the old Tk UI); CTkFont takes pixels, hence the 1.33."""
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = ctk.CTkFont(_family, max(9, round(size * 1.33 * self.s)), "bold" if bold else "normal")
        return self._fonts[key]

    def _px(self, n: float) -> int:
        return max(1, int(n * self.s))

    # ----------------------------------------------------------------- build
    def _build(self) -> None:
        pad = self._px(10)
        self._build_header(pad)
        self._build_buttons(pad)                       # packed at the bottom first so it never gets squeezed out
        self._build_images(pad)
        self._build_cards(pad)
        self._build_tray(pad)

    def _card(self, parent) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, fg_color=C["card"], border_color=C["line"], border_width=1,
                            corner_radius=self._px(16))

    def _row(self, parent) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)

    def _label(self, parent, text="", size=10, bold=False, fg=None) -> ctk.CTkLabel:
        return ctk.CTkLabel(parent, text=text, text_color=fg or C["text"], font=self._f(size, bold), height=0)

    def _caption(self, parent, text) -> ctk.CTkLabel:
        return self._label(parent, text, 9, True, C["text"])

    def _chip(self, parent, size=8) -> ctk.CTkLabel:
        return ctk.CTkLabel(parent, text="", font=self._f(size, True), corner_radius=self._px(12),
                            height=self._px(24), padx=self._px(9))

    @staticmethod
    def _paint_chip(chip: ctk.CTkLabel, text: str, color: str) -> None:
        chip.configure(text=f"●  {text}", text_color=color, fg_color=_tint(color))

    def _button(self, parent, text, cmd, size=13, height=48) -> ctk.CTkButton:
        return ctk.CTkButton(parent, text=text, command=cmd, font=self._f(size, True), height=self._px(height),
                             corner_radius=self._px(14), text_color_disabled=C["faint"], border_color=C["line"])

    def _build_header(self, pad: int) -> None:
        bar = self._row(self)
        bar.pack(fill="x", padx=pad, pady=(self._px(6), self._px(6)))
        logo_px = self._px(30)
        self._logo = ctk.CTkImage(_logo(logo_px, C["bg"]), size=(logo_px, logo_px))
        ctk.CTkLabel(bar, text="", image=self._logo, width=logo_px).pack(side="left")
        titles = self._row(bar)
        titles.pack(side="left", padx=self._px(10))
        self._label(titles, "Egg Inspector", 13, True).pack(anchor="w")
        if getattr(self.backend, "simulated_results", False):          # bring-up without the trained model
            self._label(titles, "⚠  Simulated results: no model loaded", 8, True, C["amber"]).pack(anchor="w")
        elif getattr(self.backend, "mode_note", ""):                   # e.g. photos from a folder on a PC
            self._label(titles, self.backend.mode_note, 8, True, C["blue"]).pack(anchor="w")
        else:
            self._label(titles, "Micro-fissure detection with UV-C disinfection", 8, fg=C["muted"]).pack(anchor="w")
        self.exit_btn = self._button(bar, "Exit", self._quit, size=9, height=30)   # touchscreen has no Esc key
        self.exit_btn.configure(width=self._px(60), corner_radius=self._px(10))
        self.exit_btn.pack(side="right")
        self.live_btn = None
        if self.cam is not None and getattr(self.backend, "model", None) is not None:   # camera + real model
            self.live_btn = self._button(bar, "Live", self._toggle_live, size=9, height=30)
            self.live_btn.configure(width=self._px(70), corner_radius=self._px(10))
            self.live_btn.pack(side="right", padx=(0, self._px(8)))
        self.clock = self._label(bar, "", 13, True)
        self.clock.pack(side="right", padx=self._px(14))
        self.chip = self._chip(bar, 9)
        self.chip.pack(side="right")
        self.status = self._label(bar, "", 9, fg=C["muted"])
        self.status.pack(side="right", padx=self._px(10))

    def _build_images(self, pad: int) -> None:
        gap = pad
        inner = (self.W - 2 * pad - gap) // 2 - 18          # two panels; minus border + inner padding
        height = int(((self.W - 2 * pad - 2 * gap) // 3 - 18) * 0.41)   # short images leave room for the tray row on 480 px screens
        size = (inner, height)
        row = self._row(self)
        row.pack(fill="x", padx=pad, pady=(0, pad))
        fonts = {"dot": self._f(7, True), "title": self._f(9, True), "hint": self._f(8), "text": self._f(8)}
        text_w = size[0] - int(height * 1.45)          # the egg photo is ~4:3; the rest of the heatmap card is text
        specs = [("Optical", "transilluminated", "optical", C["amber"], 0),
                 ("XGrad-CAM", "heatmap + what it means", "heat", C["uv"], text_w)]
        self.panels = {}
        for col, (title, hint, key, accent, tw) in enumerate(specs):
            panel = ImagePanel(row, title, hint, accent, size, fonts, self._px(10), self._px(16), self._zoom, tw)
            panel.grid(row=0, column=col, padx=(0 if col == 0 else gap // 2, 0 if col == len(specs) - 1 else gap // 2))
            self.panels[key] = panel
        row.columnconfigure(tuple(range(len(specs))), weight=1, uniform="img")
        self._zoom_fonts = {"title": self._f(11, True), "hint": self._f(9)}
        if hasattr(self.cam, "set_exposure"):                        # webcam: exposure for candling
            self._build_light(self.panels["optical"])
        self._zoomed: ZoomView | None = None

    def _build_light(self, panel: ImagePanel) -> None:
        """−/+ step the webcam exposure; Auto finds the brightest one that doesn't burn out a candled egg."""
        panel.zoom_hint.pack_forget()                             # the image is still tappable; make room
        bar = self._row(panel.head)
        bar.pack(side="right")
        small = dict(size=8, height=22)
        for text, cmd in (("Auto", self._light_auto), ("+", lambda: self._light_step(1))):
            b = self._button(bar, text, cmd, **small)
            b.configure(width=self._px(40 if text == "Auto" else 24), corner_radius=self._px(8))
            self._style(b, "secondary", True)
            b.pack(side="right", padx=(self._px(3), 0))
        self.light_val = self._label(bar, "", 8, True, C["muted"])
        self.light_val.pack(side="right", padx=self._px(3))
        b = self._button(bar, "−", lambda: self._light_step(-1), **small)
        b.configure(width=self._px(24), corner_radius=self._px(8))
        self._style(b, "secondary", True)
        b.pack(side="right")
        self._show_light()

    def _show_light(self) -> None:
        e = self.cam.exposure
        self.light_val.configure(text="☀ auto" if e is None else f"☀ {e + 1}/{self.cam.steps}")

    def _light_step(self, delta: int) -> None:
        e = self.cam.exposure
        self.cam.set_exposure(self.cam.steps // 2 if e is None else e + delta)
        self._show_light()
        self.after(400, self._check_light)               # the reader thread applies it between frames

    def _check_light(self) -> None:
        self._show_light()                               # the step count changes if it fell back to brightness
        if self.cam.mode == "none":
            self.cam.set_exposure(None)
            self._ev_light(None)

    def _light_auto(self) -> None:
        self.light_val.configure(text="☀ …")
        self._set_message("Setting the light: hold a candled egg in front of the camera…", C["uv"])
        threading.Thread(target=lambda: self.events.put(Event("light", self.cam.auto_light())),
                         daemon=True).start()

    def _ev_light(self, step: int | None) -> None:
        self._show_light()
        if step is None:
            self._set_message("This webcam doesn't let the app change its light. Dim or diffuse the candling "
                              "light, and fill the picture with the egg.", C["amber"])
        elif self.cam.mode == "brightness":
            self._set_message(f"This webcam can't change its exposure, so brightness was turned down "
                              f"({step + 1}/{self.cam.steps}). If the egg still glows white, dim or diffuse "
                              "the candling light.", C["amber"])
        else:
            self._set_message(f"Light set to {step + 1}/{self.cam.steps}: the brightest exposure that keeps the "
                              "egg's detail. Use − / + to fine-tune.", C["green"])

    def _zoom(self, img: Image.Image, title: str) -> None:
        self._close_zoom()
        height = self.btns["start"].master.winfo_y() - self._px(6)      # keep Start/Stop/Reset visible
        r = self._shown if img is self.panels["heat"].source else None  # the heatmap comes with its explanation
        self._zoomed = ZoomView(self, img, title, (self.W, height), self._zoom_fonts,
                                self._full_text(r) if r is not None else None)
        self._zoomed.place(x=0, y=0)

    def _full_text(self, r: EggResult) -> str:
        score = r.confidence if r.defective else 1 - r.confidence
        text = r.explanation or interpret(r.defective, score, self._threshold, r.facts)
        if not r.facts:
            return text
        facts = "\n".join(f"•  {line}" for line in fact_lines(r.facts))
        return f"{text}\n\nMeasured from the heatmap:\n{facts}\n\n{r.explained_by or 'local SLM: writing…'}"

    def _explain(self, r: EggResult) -> None:
        """Worker thread: the SLM words the heatmap measurements (a few seconds; longer on a Pi)."""
        try:
            text, source = self.explainer.explain(r.facts)
        finally:
            self._slm_busy = False
        self.events.put(Event("explained", (r, text, source)))

    def _ev_explained(self, data: tuple[EggResult, str, str]) -> None:
        r, r.explanation, r.explained_by = data
        if self._shown is r:
            self.panels["heat"].say(r.explanation)
            if self._zoomed is not None and self._zoomed.winfo_exists():
                self._zoomed.set_text(self._full_text(r))

    def _close_zoom(self) -> None:
        if self._zoomed is not None and self._zoomed.winfo_exists():
            self._zoomed.destroy()
        self._zoomed = None

    def _build_cards(self, pad: int) -> None:
        row = self._row(self)
        row.pack(fill="x", padx=pad, pady=(0, pad))
        row.columnconfigure(0, weight=11, uniform="card")
        row.columnconfigure(1, weight=9, uniform="card")
        self._build_result_card(row).grid(row=0, column=0, sticky="nsew", padx=(0, pad // 2))
        self._build_uvc_card(row).grid(row=0, column=1, sticky="nsew", padx=(pad // 2, 0))

    def _build_result_card(self, parent) -> ctk.CTkFrame:
        card = self._card(parent)
        top = self._row(card)
        top.pack(fill="x", padx=16, pady=(10, 0))
        self._caption(top, "Inspection result").pack(side="left")
        self.egg_tag = self._label(top, "", 8, True, C["muted"])
        self.egg_tag.pack(side="right")
        mid = self._row(card)
        mid.pack(fill="x", padx=16, pady=(6, 0))
        badge = self._px(42)
        self._badges = {st: ctk.CTkImage(_badge(badge, st), size=(badge, badge)) for st in VERDICTS}
        self.badge = ctk.CTkLabel(mid, text="", image=self._badges["idle"], width=badge, height=badge)
        self.badge.pack(side="left")
        words = self._row(mid)
        words.pack(side="left", padx=(self._px(12), 0))
        self.verdict = self._label(words, "", 17, True)
        self.verdict.pack(anchor="w")
        self.detail = self._label(words, "", 8, fg=C["muted"])
        self.detail.pack(anchor="w")
        conf = self._row(card)
        conf.pack(fill="x", padx=16, pady=(6, 12))
        self._label(conf, "Confidence", 8, fg=C["muted"]).pack(side="left")
        self.conf_val = self._label(conf, "", 9, True)
        self.conf_val.pack(side="right")
        self.conf_bar = ctk.CTkProgressBar(conf, height=self._px(6), fg_color=C["track"], corner_radius=self._px(3))
        self.conf_bar.pack(side="left", fill="x", expand=True, padx=self._px(10))
        return card

    def _build_uvc_card(self, parent) -> ctk.CTkFrame:
        card = self._card(parent)
        self.uvc_ring = UvcRing(card, self._px(80))
        self.uvc_ring.pack(side="left", padx=(16, 0), pady=10)
        side = self._row(card)
        side.pack(side="left", fill="both", expand=True, padx=(14, 16), pady=(10, 10))
        top = self._row(side)
        top.pack(fill="x")
        self._caption(top, "UV-C disinfection").pack(side="left")
        self.auto_switch = Toggle(top, self.auto_uvc, self._toggle_auto, (self._px(40), self._px(22)), C["uv"])
        self.auto_switch.pack(side="right")
        auto = self._label(top, "Auto", 8, True, C["muted"])
        auto.pack(side="right", padx=(0, self._px(6)))
        auto.bind("<Button-1>", lambda e: self.auto_switch.toggle())   # bigger touch target
        self.uvc_chip = self._chip(side, 8)
        self.uvc_chip.pack(anchor="w", pady=(4, 0))
        self.uvc_btn = self._button(side, "Run UV-C", self._btn_uvc, size=10, height=34)
        self.uvc_btn.configure(corner_radius=self._px(10))
        self.uvc_btn.pack(fill="x", side="bottom")
        return card

    def _build_tray(self, pad: int) -> None:
        card = self._card(self)
        card.pack(fill="x", padx=pad, pady=(0, pad))
        top = self._row(card)
        top.pack(fill="x", padx=16, pady=(8, 0))
        self._caption(top, "Tray").pack(side="left")
        self._label(top, "Tap an egg to review it", 8, fg=C["faint"]).pack(side="left", padx=(8, 0))
        self.timing = self._label(top, "", 8, fg=C["muted"])
        self.timing.pack(side="right")
        self.tray = TrayStrip(card, self.cfg.egg_count, self._px(38), self._px(13), self._show_egg)
        self.tray.pack(fill="x", padx=16, pady=(0, 4))
        self.message = ctk.CTkLabel(card, text="", anchor="w", font=self._f(9, True), height=self._px(26),
                                    corner_radius=self._px(10), padx=self._px(10))
        self.message.pack(fill="x", padx=10, pady=(0, 10))

    def _build_buttons(self, pad: int) -> None:
        row = self._row(self)
        row.pack(side="bottom", fill="x", padx=pad, pady=(0, pad))
        row.columnconfigure((0, 1, 2), weight=1, uniform="btn")
        self.btns = {}
        for col, (key, text, cmd) in enumerate([("start", "▶   Start", self._btn_start),
                                                ("stop", "■   Stop", self._btn_stop),
                                                ("reset", "↺   Reset", self._btn_reset)]):
            btn = self._button(row, text, cmd)
            btn.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else pad // 2, 0 if col == 2 else pad // 2))
            self.btns[key] = btn

    # --------------------------------------------------------------- display
    def _set_chip(self, text: str, color: str) -> None:
        self._paint_chip(self.chip, text, color)

    def _set_message(self, text: str, color: str | None = None) -> None:
        if not text:
            self.message.configure(text="", fg_color=C["card"])
            return
        color = color or C["text"]
        self.message.configure(text=text, text_color=color, fg_color=_tint(color))

    @staticmethod
    def _style(btn: ctk.CTkButton, kind: str, enabled: bool) -> None:
        fill, ink = BUTTONS[kind]
        secondary = kind == "secondary"
        btn.configure(state="normal" if enabled else "disabled", text_color=ink,
                      fg_color=fill if enabled else C["disabled"],
                      hover_color=C["subtle"] if secondary else _darker(fill),
                      border_width=1 if secondary and enabled else 0)

    def _set_running(self, running: bool) -> None:
        self._style(self.btns["start"], "primary", not running)
        self._style(self.btns["stop"], "danger", running)
        self._style(self.btns["reset"], "secondary", not running)
        self._style(self.uvc_btn, "uv", not running)
        self._style(self.exit_btn, "secondary", not running)
        if self.live_btn:
            self._style(self.live_btn, "uv" if self._live else "secondary", not running)

    def _set_verdict(self, state: str) -> None:
        word, color = VERDICTS[state]
        self.badge.configure(image=self._badges[state])
        self.verdict.configure(text=word, text_color=C["muted"] if state == "idle" else color)

    def _reset_view(self) -> None:
        self.results.clear()
        self.tray.reset()
        self.panels["optical"].show(None, "No image yet")
        self.panels["heat"].show(None, "No image yet")
        self.panels["heat"].say("After an inspection, this explains the result in plain words.")
        self._shown = None
        self._set_verdict("idle")
        self.detail.configure(text="Press Start to inspect the tray")
        self.egg_tag.configure(text="")
        self.conf_val.configure(text="–")
        self._set_bar(self.conf_bar, 0, C["track"])
        self._set_uvc(0, "Standby", C["muted"])
        self.timing.configure(text="")
        self.status.configure(text="")
        self._set_message("")
        self._set_chip("Ready", C["blue"])

    @staticmethod
    def _set_bar(bar: ctk.CTkProgressBar, frac: float, color: str) -> None:
        frac = max(0.0, min(1.0, frac))
        bar.configure(progress_color=color if frac > 0 else C["track"])   # CTk draws a stub dot at 0
        bar.set(frac)

    def _set_uvc(self, elapsed: int, chip: str, color: str) -> None:
        self._uvc_elapsed = elapsed
        self.uvc_ring.set(elapsed, self.cfg.uvc_seconds, color)
        self._paint_chip(self.uvc_chip, chip, color)

    def _show_uvc_outcome(self, o: UvcOutcome, cracks: bool) -> None:
        if o.status == "completed":
            self._set_uvc(self.cfg.uvc_seconds, "Completed", C["green"])
        elif o.status == "skipped":
            self._set_uvc(0, "Pending" if cracks else "Not needed", C["amber"] if cracks else C["muted"])
        else:                                          # interrupted | blocked
            self._set_uvc(self._uvc_elapsed, o.status.capitalize(), C["amber"])

    def _show_egg(self, i: int) -> None:
        self.tray.select(i)
        self.panels["optical"].show(self.results[i].optical)
        self._show_result(self.results[i], f"Egg {i + 1} of {self.cfg.egg_count}")

    def _show_result(self, r: EggResult, tag: str) -> None:
        self._shown = r
        self.panels["heat"].show(r.heatmap, "No crack, no heatmap")
        score = r.confidence if r.defective else 1 - r.confidence
        self.panels["heat"].say(r.explanation or interpret(r.defective, score, self._threshold, r.facts))
        if r.defective and r.facts and not r.explanation and not self._slm_busy:
            self._slm_busy = True                      # one SLM job at a time; Live frames meanwhile keep the fixed text
            threading.Thread(target=self._explain, args=(r,), daemon=True).start()
        bad = r.defective
        self._set_verdict("bad" if bad else "ok")
        if bad and r.facts:                            # the heatmap in a few words; tap it for the full explanation
            what = f"Micro-fissure  ·  {summary(r.facts)}"
        else:                                          # where the picture came from (a file name in photo mode)
            what = "  ·  ".join(filter(None, ["Micro-fissure" if bad else "No micro-fissure", r.source]))
        self.detail.configure(text=f"{what}  ·  {r.latency_ms / 1000:.1f} s")
        self.egg_tag.configure(text=tag)
        self.conf_val.configure(text=f"{r.confidence * 100:.1f}%")
        self._set_bar(self.conf_bar, r.confidence, C["red"] if bad else C["green"])

    # --------------------------------------------------------------- buttons
    def _btn_start(self) -> None:
        self._set_live(False)
        self._close_zoom()
        self._cancel_confirm()
        self._reset_view()
        self._set_running(True)
        self._set_chip("Inspecting", C["blue"])
        self.tray.set_state(0, "active")
        self.ctrl.start()

    def _btn_stop(self) -> None:
        self.ctrl.stop()                               # UV-C is cut by the controller within ~50 ms
        self.status.configure(text="Stopping…")

    def _btn_reset(self) -> None:
        if self.ctrl.running:
            return
        self._set_live(False)
        self._cancel_confirm()
        self._close_zoom()
        self.backend.clear_leds()
        self._reset_view()

    def _btn_uvc(self) -> None:
        """Manual UV-C. First tap arms, second tap within UVC_CONFIRM_MS starts the lamp."""
        if self.ctrl.running:
            return
        if self._confirm_job is None:
            self._style(self.uvc_btn, "warn", True)
            self.uvc_btn.configure(text="Tap again to start")
            self._confirm_job = self.after(UVC_CONFIRM_MS, self._cancel_confirm)
            return
        self._cancel_confirm()
        self._set_running(True)
        self._set_uvc(0, "Starting", C["uv"])
        self._set_chip("UV-C on", C["uv"])
        self._set_message("Manual UV-C running. Keep the chamber door closed.", C["uv"])
        self.ctrl.start_uvc()

    def _cancel_confirm(self) -> None:
        if self._confirm_job is not None:
            self.after_cancel(self._confirm_job)
            self._confirm_job = None
        self.uvc_btn.configure(text="Run UV-C")
        self._style(self.uvc_btn, "uv", not self.ctrl.running)

    def _toggle_auto(self) -> None:
        self.ctrl.auto_uvc = bool(self.auto_uvc.get())   # picked up when the current inspection ends

    # ------------------------------------------------------------------ live
    def _toggle_live(self) -> None:
        if not self.ctrl.running:
            self._set_live(not self._live)

    def _set_live(self, on: bool) -> None:
        """Live test: the Optical panel follows the camera and the model checks the sharpest recent frame non-stop."""
        if on == self._live or not self.live_btn:
            return
        self._live = on
        self._live_stop.set()                          # ends the checks of the previous session
        if hasattr(self.backend, "light"):             # the station's candling light: on while Live runs
            self.backend.light(on)
        self.live_btn.configure(text="■  Live" if on else "Live")
        self._set_running(self.ctrl.running)
        if on:
            self._live_run += 1                        # loops from an earlier Live session see this and stop
            self._live_stop = threading.Event()
            self.tray.select(None)
            self._set_chip("Live test", C["uv"])
            self._set_message("Live test: hold an egg in front of the camera. Tap Live again to stop.", C["uv"])
            threading.Thread(target=self._live_checks, args=(self._live_run, self._live_stop), daemon=True).start()
            self._live_feed(self._live_run)
        else:
            self._set_chip("Ready", C["blue"])
            self._set_message("")
            self.status.configure(text="")

    def _live_on(self, run: int) -> bool:
        return self._live and run == self._live_run

    def _live_feed(self, run: int) -> None:
        if self._live_on(run):
            frame = self.cam.live_frame()
            if frame is not None:
                self.panels["optical"].show(frame)
            fps = f"Camera {self.cam.fps:.0f} fps"
            if self.status.cget("text") != fps:
                self.status.configure(text=fps)
            self.after(LIVE_FEED_MS, self._live_feed, run)

    def _live_checks(self, run: int, stop: threading.Event) -> None:
        """Worker thread: check the sharpest frame of the last half second (least motion blur while the egg
        turns), report it, repeat. Results go through the event queue."""
        while self._live_on(run):
            picked: list = []
            try:
                if not sweep(self.cam, LIVE_WINDOW_S, 1, stop, lambda _k, f: picked.append(f)):
                    return                                # Live was switched off
            except RuntimeError as exc:                   # the camera stopped sending pictures
                self.events.put(Event("live_error", str(exc)))
                return
            frame = picked[0]
            if frame is None:                             # every frame burnt white: don't judge a white blob
                self.events.put(Event("live_bright", (run, burnt(self.cam.frame))))
                time.sleep(0.5)
                continue
            t0 = time.perf_counter()
            try:
                view = self.backend.model.predict(frame)
            except Exception as exc:                          # show it like any other failure, then stop
                self.events.put(Event("live_error", f"{type(exc).__name__}: {exc}"))
                return
            conf = view.score if view.cracked else 1 - view.score
            r = EggResult(-1, view.cracked, conf, frame, view.heatmap, (time.perf_counter() - t0) * 1000,
                          source="live", facts=view.facts)
            self.events.put(Event("live", (run, r)))

    def _ev_live(self, data: tuple[int, EggResult]) -> None:
        run, r = data
        if not self._live_on(run):                         # drop a result from a Live session that has ended
            return
        self._show_result(r, "Live")
        self._set_message("Live test: hold an egg in front of the camera. Tap Live again to stop.", C["uv"])

    def _ev_live_bright(self, data: tuple[int, float]) -> None:
        run, white = data
        if not self._live_on(run):
            return
        self._shown = None
        self._set_verdict("idle")
        self.detail.configure(text="Too bright to judge: waiting for a usable picture")
        self.egg_tag.configure(text="Live")
        self.conf_val.configure(text="–")
        self._set_bar(self.conf_bar, 0, C["track"])
        self.panels["heat"].show(None, "Waiting")
        self.panels["heat"].say("The picture is too bright: a burnt-white egg hides any crack, so the model "
                                "waits instead of guessing.")
        self._set_message(f"Too bright: {white:.0%} of the picture is burnt white. Tap Auto (Light), or dim / "
                          "diffuse the candling light.", C["amber"])

    def _ev_live_error(self, msg: str) -> None:
        self._set_live(False)
        self._ev_error(msg)

    # ---------------------------------------------------------------- events
    def _poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                getattr(self, f"_ev_{ev.kind}")(ev.data)
        except queue.Empty:
            pass
        self.after(40, self._poll)

    def _ev_status(self, text: str) -> None:
        if text.startswith(TOO_BRIGHT):                  # webcam waiting for a usable picture: make it obvious
            self._too_bright = True
            self.status.configure(text="Waiting: too bright")
            self._set_message(text, C["amber"])
            frame = self.cam.live_frame()            # show the camera so the light can be adjusted while waiting
            if frame is not None:
                self.panels["optical"].show(frame)
            return
        if self._too_bright:                             # usable again: clear the warning
            self._too_bright = False
            self._set_message("")
        self.status.configure(text=text)

    def _ev_egg(self, r: EggResult) -> None:
        self.results[r.index] = r
        self.tray.set_state(r.index, "bad" if r.defective else "ok")
        if r.index + 1 < self.cfg.egg_count:
            self.tray.set_state(r.index + 1, "active")
        self._show_egg(r.index)

    def _ev_uvc(self, data: tuple[int, int]) -> None:
        self._set_uvc(data[0], "Running", C["uv"])
        self._set_chip("UV-C on", C["uv"])
        self.status.configure(text="UV-C treatment")

    def _ev_done(self, s: Summary) -> None:
        cracks = bool(s.defective)
        self._show_uvc_outcome(s.uvc, cracks)
        settled = not cracks or s.uvc.status == "completed"
        self._set_chip("Done" if settled else "Check", C["green"] if settled else C["amber"])
        self.timing.configure(text=f"Inspection time {s.inspection_s:.1f} s")
        if cracks:
            flagged = ", ".join(f"#{i + 1}" for i in s.defective)
            tail = "UV-C completed." if s.uvc.status == "completed" else s.uvc.note
            self._set_message(f"Remove flagged eggs (LED on): {flagged}.  {tail}", C["red"])
        else:
            self._set_message(f"No micro-fissures found.  {s.uvc.note}", C["green"])
        self.status.configure(text="Finished")

    def _ev_uvc_done(self, o: UvcOutcome) -> None:
        self._show_uvc_outcome(o, cracks=False)
        ok = o.status == "completed"
        self._set_chip("UV-C done" if ok else "Check", C["green"] if ok else C["amber"])
        self._set_message("Manual UV-C completed." if ok else o.note, C["green"] if ok else C["amber"])
        self.status.configure(text="")

    def _ev_stopped(self, _=None) -> None:
        self._set_chip("Stopped", C["amber"])
        self._set_message("Stopped by operator before the batch finished.", C["amber"])
        self.status.configure(text="")

    def _ev_error(self, msg: str) -> None:
        self._set_chip("Error", C["red"])
        self._set_message(msg, C["red"])
        self.status.configure(text="")

    def _ev_finished(self, _=None) -> None:
        self.tray.clear_active()
        self._set_running(False)

    # ------------------------------------------------------------------ misc
    def _tick_clock(self) -> None:
        self.clock.configure(text=time.strftime("%H:%M"))
        self.after(1000, self._tick_clock)

    def _quit(self) -> None:
        self._live = False
        self._live_stop.set()
        self.ctrl.stop()
        self.ctrl.join()
        self.backend.close()
        self.destroy()
