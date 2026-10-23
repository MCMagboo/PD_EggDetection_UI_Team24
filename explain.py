"""Plain-language explanation of an XGrad-CAM heatmap (the explainable-AI part).

1. cam_facts() measures the heatmap: where it is strongest, how much of the photo it covers, how many separate
   spots it has, and how much of it lies inside the box of the detected crack. That last number says whether the
   model looked at the crack itself, or at dirt, a shadow or the background.
2. Explainer.explain() has a small language model (SLM) running on this machine (Ollama) put those measurements
   into 2-3 plain sentences. The SLM never sees the image; it only rewords the measured facts, so it can't
   describe things that aren't there. If Ollama isn't running, describe() gives the same facts in fixed wording.

Ollama: https://ollama.com (Windows installer, or on the Pi: curl -fsSL https://ollama.com/install.sh | sh),
then once per machine: ollama pull qwen2.5:1.5b
"""
from __future__ import annotations

import numpy as np

HOT = 0.5                       # heatmap cells at >= 50 % of the peak count as "hot"


def _place(x: float, y: float) -> str:
    """Position (0..1 across, 0..1 down) in words: 'upper left', 'centre', 'lower right', ..."""
    v = "upper" if y < 1 / 3 else "middle" if y < 2 / 3 else "lower"
    h = "left" if x < 1 / 3 else "centre" if x < 2 / 3 else "right"
    return "centre" if (v, h) == ("middle", "centre") else f"{v} {h}"


def _spots(hot: np.ndarray) -> int:
    """Separate hot areas (cells touching side by side count as one)."""
    seen, count = np.zeros_like(hot, bool), 0
    for start in zip(*np.nonzero(hot)):
        if seen[start]:
            continue
        count += 1
        stack = [start]
        seen[start] = True
        while stack:
            y, x = stack.pop()
            for n in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= n[0] < hot.shape[0] and 0 <= n[1] < hot.shape[1] and hot[n] and not seen[n]:
                    seen[n] = True
                    stack.append(n)
    return count


def cam_facts(cam: np.ndarray, box: tuple[float, float, float, float], score: float, threshold: float) -> dict:
    """Measure a heatmap. `cam` covers the whole photo (any size, >= 0); `box` is the detected crack as
    (x1, y1, x2, y2) in 0..1 of the photo's width and height."""
    cam = cam / cam.max() if cam.max() > 0 else cam
    h, w = cam.shape
    hot = cam >= HOT
    ys, xs = np.nonzero(hot)
    weight = cam[hot]
    cx = float(((xs + 0.5) * weight).sum() / weight.sum() / w) if len(xs) else 0.5
    cy = float(((ys + 0.5) * weight).sum() / weight.sum() / h) if len(ys) else 0.5
    x1, y1 = int(np.floor(box[0] * w)), int(np.floor(box[1] * h))
    x2, y2 = max(x1 + 1, int(np.ceil(box[2] * w))), max(y1 + 1, int(np.ceil(box[3] * h)))
    total = float(cam.sum())
    return {
        "score": round(float(score), 3),
        "threshold": round(float(threshold), 3),
        "box_place": _place((box[0] + box[2]) / 2, (box[1] + box[3]) / 2),
        "box_area": round(float((box[2] - box[0]) * (box[3] - box[1])), 3),
        "hot_place": _place(cx, cy),
        "hot_area": round(float(hot.mean()), 3),
        "hot_spots": _spots(hot),
        "on_crack": round(float(cam[y1:y2, x1:x2].sum()) / total, 3) if total > 0 else 0.0,
    }


def agreement(f: dict) -> str:
    """How well the heatmap backs up the detection."""
    return "strong" if f["on_crack"] >= 0.6 else "partial" if f["on_crack"] >= 0.3 else "weak"


def summary(f: dict) -> str:
    """One short line for the result card."""
    return f"heatmap {f['hot_place']}, {f['on_crack']:.0%} on the crack"


def fact_lines(f: dict) -> list[str]:
    spots = f"{f['hot_spots']} spot" + ("s" if f["hot_spots"] != 1 else "")
    return [f"Crack found with {f['score']:.0%} confidence (threshold {f['threshold']:.0%})",
            f"Detected crack: {f['box_place']} of the photo, {f['box_area']:.0%} of its area",
            f"Heatmap strongest in the {f['hot_place']}; hot area {f['hot_area']:.0%} of the photo, {spots}",
            f"{f['on_crack']:.0%} of the heatmap lies on the detected crack ({agreement(f)} agreement)"]


def verdict(f: dict) -> tuple[str, str]:
    """(conclusion, action) decided by the code from the measurements; the SLM only puts them into words."""
    level = agreement(f)
    if level == "strong":
        return ("The heatmap supports the detection: the model looked at the crack.",
                "Remove the egg as cracked.")
    if level == "partial":
        return ("The heatmap partly supports the detection: part of it lies away from the crack.",
                f"Remove the egg, and look at it around the {f['hot_place']} as well.")
    return ("The heatmap does not support the detection: the model mostly looked away from the crack, "
            "for example at dirt, a shadow or the background.",
            "Check this egg by hand before removing it.")


def describe(f: dict) -> str:
    """Fixed-wording explanation: used when the SLM isn't available, and as a check on what it should say."""
    first = (f"The model found a crack in the {f['box_place']} of the egg photo "
             f"({f['score']:.0%} confidence, threshold {f['threshold']:.0%}).")
    level = agreement(f)
    if level == "strong":
        rest = (f"Most of the heatmap ({f['on_crack']:.0%}) lies on that crack, so the decision is based on the "
                "crack itself.")
    elif level == "partial":
        rest = (f"Only {f['on_crack']:.0%} of the heatmap lies on the crack and the rest is spread over "
                f"{f['hot_spots']} spot(s), so look at the egg around the {f['hot_place']} as well.")
    else:
        rest = (f"Only {f['on_crack']:.0%} of the heatmap lies on the detected crack, so the model may have reacted "
                "to dirt, a shadow or the background. Check this egg by hand.")
    return f"{first} {rest}"


def interpret(defective: bool, score: float, threshold: float, facts: dict | None) -> str:
    """Plain-language reading of any result, shown next to the heatmap straight away."""
    if not defective:
        return (f"No crack found. The model's highest crack score was {score:.0%}, below the {threshold:.0%} "
                "needed to reject the egg, so it passes.")
    if facts:
        return describe(facts)
    return (f"Crack found ({score:.0%} sure, threshold {threshold:.0%}). The heatmap shows where the model "
            "looked; the detailed reading needs the local model.")


SYSTEM = ("You write for the operator of a duck egg inspection station. Rewrite the facts you are given as 2 or 3 "
          "short, plain sentences (under 45 words in all): where the crack is, the Conclusion, and the Action. "
          "Keep the meaning of the "
          "Conclusion and the Action exactly. Use only the facts given; do not add numbers, colours or causes. "
          "Say 'the egg', and do not start with 'The duck-egg crack detector'.")


class Explainer:
    """Asks the SLM (Ollama on this machine) to word the facts; falls back to describe()."""

    def __init__(self, model: str, url: str, timeout: float = 120):
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout

    def explain(self, f: dict) -> tuple[str, str]:
        """Returns (text, source); source names the SLM, or why the fixed wording was used."""
        import httpx

        conclusion, action = verdict(f)
        lines = fact_lines(f) + [f"Conclusion: {conclusion}", f"Action: {action}"]
        prompt = "Facts:\n" + "\n".join(f"- {line}" for line in lines) + "\nExplanation:"
        try:
            reply = httpx.post(f"{self.url}/api/generate", timeout=httpx.Timeout(self.timeout, connect=2),
                               json={"model": self.model, "system": SYSTEM, "prompt": prompt, "stream": False,
                                     "options": {"temperature": 0.2, "num_predict": 90}})
        except httpx.TransportError:
            return describe(f), "fixed wording (Ollama is not running on this machine)"
        if reply.status_code == 404:
            return describe(f), f"fixed wording (SLM not installed: ollama pull {self.model})"
        if not reply.is_success:
            return describe(f), f"fixed wording (Ollama error {reply.status_code})"
        text = reply.json().get("response", "").strip()
        if not text:
            return describe(f), "fixed wording (the SLM returned nothing)"
        if not _consistent(text, f):
            return describe(f), "fixed wording (the SLM's answer contradicted the measurements)"
        return text, f"written by {self.model} (local SLM) from the measurements"

    def installed(self) -> list[str] | None:
        """SLMs Ollama has on this machine, or None if Ollama isn't running."""
        import httpx

        try:
            return [m["name"] for m in httpx.get(f"{self.url}/api/tags", timeout=httpx.Timeout(5, connect=2))
                    .json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            return None

    def pick(self) -> bool:
        """Use the configured SLM, or the size the installer chose for this machine's memory."""
        have = self.installed() or []
        for name in (self.model, "qwen2.5:1.5b", "qwen2.5:0.5b"):
            if name in have:
                self.model = name
                return True
        return False

    def warm(self) -> None:
        """Load the SLM in the background so the first explanation doesn't wait for it (~45 s cold)."""
        import httpx

        self.pick()
        try:
            httpx.post(f"{self.url}/api/generate", json={"model": self.model, "keep_alive": "30m"},
                       timeout=httpx.Timeout(300, connect=2))
        except httpx.HTTPError:
            pass                                 # not running: explanations use the fixed wording


def _consistent(text: str, f: dict) -> bool:
    """The SLM must keep the code's action: 'by hand' only, and always, when the heatmap doesn't back the crack."""
    by_hand = "by hand" in text.lower()
    return by_hand if agreement(f) == "weak" else not by_hand
