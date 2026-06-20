"""Host-side assembly: caption the master, cut clips, manifest, gif.

Reads the in-rig recording (master_raw.mp4 + timeline.json), shifts the timeline
to be video-relative (by the recorder's lead), burns captions (per-step three-way
token stats + a closing summary card), cuts editable per-segment clips at settled
boundaries, writes a manifest, and renders a README gif.

    python scripts/assemble_demo.py rig/out docs/assets

Outputs into <assets>/:
  cua-drive.mp4            captioned master
  cua-drive.gif            README gif
  clips/NN-<label>.mp4     editable segment clips
  clips/manifest.json      edit list + provenance (tokens, perceived, verified)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cerebellum_cua.tutorial.captions import burn_captions, summary_card
from cerebellum_cua.tutorial.clips import (
    build_manifest,
    ffmpeg_cut_argv,
    plan_segments,
)


def _run(argv: list[str]) -> None:
    res = subprocess.run(argv, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(argv)}\n{res.stderr[-400:]}")


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "rig/out")
    assets = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/assets")
    clips_dir = assets / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    data = json.loads((out_dir / "timeline.json").read_text())
    lead = float(data.get("lead", 0.0))
    totals = data["totals"]
    timeline = data["timeline"]

    # Shift to video-relative time and fold the closing summary into the last step.
    for step in timeline:
        step["start"] = round(step["start"] + lead, 3)
        step["end"] = round(step["end"] + lead, 3)
    timeline[-1]["caption"] = timeline[-1]["caption"] + "\n\n" + summary_card(totals)

    master_raw = str(out_dir / "master_raw.mp4")
    master = str(assets / "cua-drive.mp4")
    print(f"burning captions -> {master}")
    burn_captions(master_raw, timeline, master, fontsize=24)

    # Cut editable clips at settled step boundaries.
    segments = plan_segments(timeline)
    for seg in segments:
        out = str(clips_dir / f"{seg['index']:02d}-{seg['label']}.mp4")
        print(f"clip {seg['index']:02d} [{seg['start']:.1f}-{seg['end']:.1f}] -> {out}")
        _run(ffmpeg_cut_argv(master, seg, out))

    manifest = build_manifest(timeline, totals, segments)
    (clips_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # README gif — two-pass palette for small size + good quality.
    gif = str(assets / "cua-drive.gif")
    palette = str(out_dir / "palette.png")
    filt = "fps=8,scale=720:-1:flags=lanczos"
    print(f"rendering gif -> {gif}")
    _run(["ffmpeg", "-y", "-i", master, "-vf", f"{filt},palettegen=max_colors=64",
          palette])
    _run(["ffmpeg", "-y", "-i", master, "-i", palette, "-lavfi",
          f"{filt}[x];[x][1:v]paletteuse=dither=bayer", gif])

    print(f"done: a11y={totals['a11y_tokens']} focused={totals['shot_tokens']} "
          f"full={totals['full_tokens']} clips={len(segments)}")


if __name__ == "__main__":
    main()
