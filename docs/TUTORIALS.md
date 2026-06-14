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
