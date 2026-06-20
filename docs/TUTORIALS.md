# Captioned tutorials

The tutorial module drives a real application through the engine while drawing a
caption on screen for each step, and records the result to video for docs. It is
three pieces: an authoring model (JSON), a runner that produces a timeline, and a
caption-burn step that overlays that timeline onto a recording.

## 1. Author a tutorial (JSON)

A tutorial is a title and an ordered list of steps. Each step has a `caption`
(drawn on screen), an `action`, a `name`, an `args` dict, and a `hold` (seconds
the caption stays up). The three actions:

| action  | what it does                                  | uses `name` / `args`            |
|---------|-----------------------------------------------|---------------------------------|
| `skill` | run a named skill via the `run_skill` handler | `name` = skill, `args` = kwargs |
| `op`    | run an engine operation handler               | `name` = op, `args` = payload   |
| `pause` | hold the caption with no action               | ignored                         |

Example (`examples/tutorials/gedit_basics.json`):

```json
{
  "title": "gedit basics",
  "steps": [
    {"caption": "cerebellum-cua driving a real app, via the accessibility tree",
     "action": "pause", "hold": 2.5},
    {"caption": "Type into the document", "action": "skill", "name": "type_into",
     "args": {"value": "Hello from a captioned tutorial.", "role": "EDIT"}, "hold": 3.0},
    {"caption": "It can read the screen too", "action": "op", "name": "screenshot",
     "args": {}, "hold": 2.5}
  ]
}
```

Load and validate it without an engine:

```python
import json
from cerebellum_cua.tutorial import Tutorial
tutorial = Tutorial.from_dict(json.load(open("examples/tutorials/gedit_basics.json")))
```

## 2. Run it (produce a timeline)

`run_tutorial(engine, tutorial)` runs each step through the engine handlers and
returns:

```json
{
  "title": "gedit basics",
  "timeline": [
    {"caption": "...", "start": 0.0, "end": 2.5, "ok": true, "result_summary": "pause"}
  ],
  "success": true
}
```

`start`/`end` are second offsets from the first step. A step that fails is
recorded with `"ok": false` and the run continues — it never crashes the
recording session.

The driver script wires up an engine for the on-screen session:

```bash
PYTHONPATH=src python3 scripts/run-tutorial.py examples/tutorials/gedit_basics.json
```

## 3. Record in the VM, then burn captions

The recording harness brings up an isolated X11 desktop, launches the app,
records the screen with ffmpeg, and runs a demo script against it. Point it at a
demo that runs the tutorial:

```bash
scripts/record-demo.sh path/to/your_demo.py rig/out
```

That produces `rig/out/demo.mp4` plus `before.png` / `after.png`. The demo should
run `run_tutorial` and write the timeline to a JSON file alongside the video.

Finally overlay the captions onto the recording:

```python
import json
from cerebellum_cua.tutorial import burn_captions
timeline = json.load(open("rig/out/timeline.json"))["timeline"]
burn_captions("rig/out/demo.mp4", timeline, "rig/out/demo_captioned.mp4")
```

`build_drawtext_filter(timeline)` is the pure function behind the overlay: it
returns the ffmpeg `drawtext` chain that shows each caption centered along the
bottom during its `[start, end]` window. `burn_captions` wraps it behind a
`shutil.which("ffmpeg")` guard and raises a typed `TutorialError` when ffmpeg is
absent. To produce a GIF instead of an MP4, pass a `.gif` output path to ffmpeg
or convert the captioned MP4 with a separate ffmpeg call.

## The self-recorded demo (`cua-drive`)

`docs/assets/cua-drive.mp4` (and `.gif`) is a captioned screencast of
cerebellum-cua driving gedit end to end — type a line → take a **focused**
screenshot of one widget → open the hamburger menu → click a menu item → read the
screen back — entirely through the accessibility tree. It was produced by the rig
*running cerebellum-cua*: the agent drove the app and the recording, then captions
were burned on. Nothing in the perception loop is a screenshot.

Each caption shows the element CUA perceived and a **three-way token estimate** for
that step: the structured a11y matrix it actually sent, a **focused** screenshot of
just that element, and a **full** screenshot. The closing card totals them — on
this run **~781 a11y tokens vs ~9,555 for full screenshots, ~12.2× cheaper**
(estimates: the gateway's char/token heuristic for the matrix, and Anthropic's
`(w·h)/750` image-token formula for the shots). The point isn't that screenshots
are useless — the tool offers screenshot/focused-shot/vision paths for
non-accessible UIs — it's that the a11y tree is the cheapest *and* most structured
default.

### How it's built (reproducible)

1. **Author + verify the flow.** `examples/tutorials/gedit_drive.json` is the
   tutorial; `scripts/probe_flow.py` runs it in the rig and must report
   `ALL_OK True` on five consecutive runs before recording (zero-flake gate).
2. **Record in the rig.** `scripts/record_tutorial.py` (run via the rig with
   `DEMO=/work/scripts/record_tutorial.py`) records the display in sync with the
   tutorial and writes `rig/out/master_raw.mp4` + `rig/out/timeline.json`
   (enriched with the three-way token figures).
3. **Assemble on the host.** `python scripts/assemble_demo.py rig/out docs/assets`
   burns the captions, cuts editable per-segment clips at settled boundaries
   (`docs/assets/clips/NN-*.mp4`), writes `clips/manifest.json` (each clip's
   caption, times, perceived element, tokens, and `verified` flag), and renders
   the README gif.

The clips are cut at settled step boundaries, so each one starts from a quiet
frame (never mid-action) and they concatenate back into the master — an edit list
you can rearrange. The flake the menu/click path first exposed (element actions
hard-failing when an ephemeral popover node can't be re-acquired) was fixed at the
source: `click` now falls back to a coordinate click at the element's known box,
and `type_into` recovers the same way — so menu/popover navigation is reliable.
