"""Checks for explain.py. Run from the folder that contains egg_inspector/:  python -m egg_inspector.test_explain"""
import numpy as np

from .explain import Explainer, _consistent, agreement, cam_facts, describe, summary, verdict


def _cam(spots):
    """12x12 heatmap with a hot square at each (row, col)."""
    cam = np.zeros((12, 12))
    for r, c in spots:
        cam[r:r + 2, c:c + 2] = 1.0
    return cam


def test_heatmap_on_the_crack():
    f = cam_facts(_cam([(1, 1)]), (0.0, 0.0, 0.34, 0.34), 0.95, 0.83)     # hot spot inside the box, upper left
    assert f["hot_place"] == "upper left" and f["box_place"] == "upper left"
    assert f["hot_spots"] == 1 and f["on_crack"] == 1.0 and agreement(f) == "strong"
    assert "100% on the crack" in summary(f) and "based on the crack itself" in describe(f)


def test_heatmap_away_from_the_crack():
    f = cam_facts(_cam([(9, 9), (1, 9)]), (0.0, 0.0, 0.34, 0.34), 0.9, 0.83)   # two spots, both outside the box
    assert f["hot_spots"] == 2 and f["on_crack"] == 0.0 and agreement(f) == "weak"
    assert "Check this egg by hand" in describe(f)


def test_guard_rejects_a_contradicting_answer():
    strong = cam_facts(_cam([(1, 1)]), (0.0, 0.0, 0.34, 0.34), 0.95, 0.83)
    weak = cam_facts(_cam([(9, 9)]), (0.0, 0.0, 0.34, 0.34), 0.95, 0.83)
    assert not _consistent("Crack found. Check the egg by hand.", strong)     # by hand although supported
    assert not _consistent("Crack found. Remove the egg.", weak)                # drops the by-hand check
    assert _consistent("Crack in the centre; the heatmap backs it. Remove the egg.", strong)
    assert "by hand" in verdict(weak)[1] and "Remove" in verdict(strong)[1]


def test_falls_back_without_ollama():
    f = cam_facts(_cam([(5, 5)]), (0.4, 0.4, 0.6, 0.6), 0.9, 0.83)
    text, source = Explainer("qwen2.5:1.5b", "http://127.0.0.1:9").explain(f)     # nothing listens on port 9
    assert text == describe(f) and "not running" in source


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
