"""In-rig recorder: record the display while running a tutorial, in sync.

Runs INSIDE the rig (display :99, ffmpeg, gedit up). Starts ffmpeg recording the
full screen, runs the tutorial so its timeline origin aligns with the recording,
then enriches the timeline with the three-way token figures (a11y matrix per step,
a focused-shot estimate for the acted element, and a full-screen-shot estimate)
and writes everything to ``$OUT``.

Outputs (to $OUT, default /rig/out):
  master_raw.mp4   the un-captioned recording
  timeline.json    {title, timeline[], totals, lead, screen:[w,h]}

The host side then shifts the timeline by ``lead`` (video-relative), burns
captions, cuts clips, and builds the manifest.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

from cerebellum_cua.cli.engine import CuaEngine
from cerebellum_cua.tutorial import Tutorial, run_tutorial
from cerebellum_cua.tutorial.tokens import bbox_image_tokens, image_tokens

#: Seconds to let ffmpeg warm up before the tutorial's first step (the recording
#: leads the timeline by this much; the host shifts captions/cuts by it).
_LEAD = 1.0


def _screen_size() -> tuple[int, int]:
    size = os.environ.get("SCREEN_SIZE", "1280x800x24")
    w, h = size.split("x")[:2]
    return int(w), int(h)


def _enrich(timeline: list[dict], screen: tuple[int, int]) -> dict:
    """Add focused/full shot token estimates per step; return three-way totals.

    Only steps that touch the screen (an action, a perceive, a read) count toward
    the totals — a ``pause`` is just a caption and costs nothing. The focused-shot
    figure uses the acted element's box (``bbox`` recorded by the runner); the
    full-shot figure is one whole-screen capture for that step.
    """
    full = image_tokens(*screen)
    totals = {"a11y_tokens": 0, "shot_tokens": 0, "full_tokens": 0}
    for step in timeline:
        a11y = int(step.get("tokens", 0))
        box = step.get("bbox")
        is_perceive = a11y > 0 or box is not None
        shot = bbox_image_tokens(tuple(box)) if box else (full if is_perceive else 0)
        step["shot_tokens"] = shot
        step["full_tokens"] = full if is_perceive else 0
        totals["a11y_tokens"] += a11y
        totals["shot_tokens"] += shot
        totals["full_tokens"] += full if is_perceive else 0
    return totals


def main() -> None:
    out = os.environ.get("OUT", "/rig/out")
    flow = os.environ.get("FLOW", "/work/examples/tutorials/gedit_drive.json")
    display = os.environ.get("DISPLAY", ":99")
    screen = _screen_size()
    tut = Tutorial.from_dict(json.load(open(flow)))

    rec = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "x11grab", "-video_size", f"{screen[0]}x{screen[1]}",
         "-framerate", "15", "-i", display, f"{out}/master_raw.mp4"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(_LEAD)  # ffmpeg warmup; the timeline origin starts after this.

    eng = CuaEngine(db_dsn=f"{out}/record.db", secret="x",
                    capture_backend_kind="atspi", visible_cursor=True,
                    verify_actions=True)
    try:
        result = run_tutorial(eng, tut)
        totals = _enrich(result["timeline"], screen)
    finally:
        eng.close()
        if rec.stdin:
            rec.stdin.write(b"q")
            rec.stdin.close()
        try:
            rec.wait(timeout=15)
        except subprocess.TimeoutExpired:
            rec.terminate()

    payload = {
        "title": result["title"],
        "timeline": result["timeline"],
        "totals": totals,
        "lead": _LEAD,
        "screen": list(screen),
        "success": result["success"],
    }
    with open(f"{out}/timeline.json", "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"RECORDED success={result['success']} totals={totals}")


if __name__ == "__main__":
    main()
