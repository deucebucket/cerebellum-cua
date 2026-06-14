"""CLI for the adjacent media pipeline.

Usage::

    python -m cerebellum_cua.media <in.mp4> --keep-motion -o <out.mp4>

Probes the input, detects motion segments, renders the kept segments with
crossfade transitions, and prints the structured cut-list as JSON. Every media
tool is guarded: if ffmpeg/ffprobe is missing the command exits non-zero with a
clear message rather than a traceback.
"""

from __future__ import annotations

import argparse
import json
import sys

from cerebellum_cua.media.edit import cut_list, render
from cerebellum_cua.media.errors import MediaError
from cerebellum_cua.media.events import detect_motion_segments
from cerebellum_cua.media.probe import probe


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the media CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m cerebellum_cua.media",
        description="Detect motion segments in a video and render them with transitions.",
    )
    parser.add_argument("input", help="Source video path.")
    parser.add_argument(
        "--keep-motion", action="store_true",
        help="Keep only segments where visual activity is detected.",
    )
    parser.add_argument("-o", "--output", help="Output video path (required to render).")
    parser.add_argument(
        "--threshold", type=float, default=0.3,
        help="Scene-change sensitivity in [0,1]; lower catches subtler motion.",
    )
    parser.add_argument("--pad", type=float, default=0.5, help="Per-event context padding (s).")
    parser.add_argument(
        "--merge-gap", type=float, default=1.0,
        help="Merge segments separated by no more than this gap (s).",
    )
    parser.add_argument("--transition", default="fade", help="xfade transition name.")
    parser.add_argument("--trans-dur", type=float, default=0.5, help="Crossfade duration (s).")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the media CLI. Returns a process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        meta = probe(args.input)
        segments = detect_motion_segments(
            args.input, threshold=args.threshold,
            pad=args.pad, merge_gap=args.merge_gap,
        )
        cuts = cut_list(segments)
        rendered: str | None = None
        if args.keep_motion and args.output:
            if segments:
                rendered = render(
                    args.input, segments, args.output,
                    transition=args.transition, trans_dur=args.trans_dur,
                    with_audio=meta["has_audio"],
                )
        report = {
            "input": args.input,
            "duration": meta["duration"],
            "has_audio": meta["has_audio"],
            "segments": len(cuts),
            "cut_list": cuts,
            "output": rendered,
        }
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    except MediaError as exc:
        print(f"media error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
