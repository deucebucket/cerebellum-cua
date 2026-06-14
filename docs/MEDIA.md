# Media pipeline (adjacent module)

`cerebellum_cua.media` is an **adjacent, self-contained capability** — a sibling
to the UI capture/control core, not part of it. Its job is to let an agent
understand and edit video *token-cheaply*, without the LLM ever watching pixels
frame-by-frame. The model reasons only over a structured **cut-list**; the actual
pixels stay in the file and are touched only by ffmpeg.

## Pipeline

```
probe(path)                       # ffprobe -> {duration, fps, width, height, vcodec, has_audio}
  └─> detect_scene_cuts / detect_motion_segments
        # ffmpeg select='gt(scene,threshold)',showinfo -> pts_time timestamps
        └─> segments_from_timestamps(timestamps, fps, duration, pad, merge_gap)
              # cluster events into padded, merged (start, end) segments
              └─> cut_list(segments)            # [{start, end, duration}]  <- LLM sees only this
                    └─> render(in, segments, out)
                          # build_trim_concat_xfade_cmd -> ffmpeg trim + xfade
```

Each stage is split into a **pure** function and a **guarded** subprocess
wrapper. The pure halves — `parse_ffprobe`, `parse_showinfo`,
`segments_from_timestamps`, `build_trim_concat_xfade_cmd`, `cut_list` — are
unit-testable on captured samples with no binaries present. The wrappers
(`probe`, `detect_*`, `render`, `transcribe`) locate their tool with
`shutil.which` and raise `MediaError` if it is missing, so the package imports
cleanly on a host with neither ffmpeg nor whisper installed.

## The silent-visual-event use case

The defining case: a **silent clip where one object crosses an otherwise-static
frame**. There is no audio to transcribe and almost nothing changes between most
frames — so watching it frame-by-frame is the worst possible use of tokens.

Flow:

1. `detect_motion_segments(path, threshold=...)` runs ffmpeg's scene/frame-
   difference `select` filter. The static background scores near zero and is
   ignored; the moving object produces a short run of change-events.
2. `segments_from_timestamps` pads each event and merges the run into one (or a
   few) contiguous `(start, end)` segments.
3. `cut_list(segments)` yields `[{start, end, duration}]` — typically a handful
   of rows. **This is the only video information the LLM receives.**
4. `render(in, segments, out)` trims those segments and crossfades them into a
   short edit containing only the moments with motion.

The LLM decides *which* segments to keep / reorder / drop by editing the
cut-list; it never ingests frames.

## The with-audio path

For clips where speech carries the meaning, `transcribe(path)` (optional
`media` extra, `faster-whisper`, falling back to `whisper`) returns timed
`[{start, end, text}]` segments the agent can reason over and align with the
visual segments. The silent case ignores this entirely.

## CLI

```
python -m cerebellum_cua.media <in.mp4> --keep-motion -o <out.mp4>
```

Probes the input, detects motion segments, renders the kept segments with
transitions, and prints the cut-list as JSON. Without `-o` it just reports the
cut-list. If ffmpeg/ffprobe is missing it exits non-zero with a clear message.

## Honest caveats

- **No off-the-shelf "UFO"/object class.** This module finds *where activity
  happens* (motion + scene-change anomaly), not *what* the object is.
  Identifying the object would require a separate step — e.g. running a sparse
  VLM on a few candidate frames from the detected segments, rather than the whole
  clip. That is intentionally out of scope here.
- **ffmpeg, ffprobe, and whisper are external.** ffmpeg/ffprobe are *system*
  tools (install FFmpeg); the transcript backend is the optional `media` extra.
  None are bundled, and none are required to import the module or run the pure
  functions / tests.
- **Sensitivity tuning matters.** Small or fast-moving objects may fall below the
  default scene threshold. Lower `threshold`, reduce `pad`, and reduce
  `merge_gap` to catch brief events; raise them to suppress noise. There is no
  universally correct setting — it depends on the footage.
- **Motion-vector optimization is noted, not implemented.** Motion can also be
  read from codec motion vectors via ffmpeg `-flags2 +export_mvs` (with the
  `codecview` filter), avoiding a full decode+difference pass. This is recorded
  as a future optimization; the current path uses the scene-change filter.
- **Adjacent module, not the UI core.** Nothing here participates in the JSONL
  protocol, the matrix, or the capture seam. It is a separate capability that
  happens to live in the same package.
