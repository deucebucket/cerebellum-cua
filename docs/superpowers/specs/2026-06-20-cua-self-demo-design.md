# Spec: CUA self-recorded, captioned, token-annotated demo

Date: 2026-06-20
Status: approved (pending spec review)

## 1. Goal

Produce a short, polished, **continuous-video** screencast in which
cerebellum-cua navigates a real application (gedit) — typing, clicking a toolbar
button, opening the hamburger menu, and selecting a menu item — driven entirely
through the accessibility tree (no screenshots in the perception loop). On-screen
captions show, per step, the element CUA perceived and the **real** token cost of
that step, contrasted with what an equivalent screenshot would cost. A closing
card gives the **overall total and comparison**. The video is produced by the rig
that runs CUA, and a short written piece explains that the demo drove and recorded
itself.

The deeper goal: the menu/click navigation is deliberately the hard path. Wherever
it flakes, that is a real weakness in the tool to fix. The flow must be **dialed in
to zero flake** — verified to run deterministically — before anything is recorded.
Hardening that path is the primary engineering work; the video is the artifact that
proves it.

## 2. Success criteria

- The authored flow runs in the rig and **every step succeeds, N=5 consecutive
  runs, with zero failures** before recording.
- The final video shows each action actually happening in gedit, with legible
  captions that match what occurred.
- Per-step token numbers are real (measured from the same run that is recorded),
  not fabricated; the screenshot-equivalent is a clearly-labeled estimate from a
  documented formula.
- A closing card shows total a11y tokens, total screenshot-equivalent tokens, and
  the ratio.
- The token comparison is **fair**: it states honestly what each side does and
  does not include (see §6). No strawman.
- The artifact looks professional: clean captions, no glitches, sensible pacing.

## 3. On-screen flow

Built only from steps verified to land in the rig (see §5). Working hypothesis,
to be confirmed/adjusted by probing:

1. `pause` — intro caption.
2. `skill type_into` — type a short note into the editor.
3. `op build_matrix` + `skill click` — click a real toolbar button (e.g. "Open"
   or "Save"); re-capture first so the click targets a freshly-perceived element.
4. `skill click` (Menu/hamburger) — open the popover menu; **re-capture** so the
   popover's items exist as perceived elements.
5. `skill click` — select a menu item from the now-perceived popover.
6. `op read_text` — read the screen back, proving perception.
7. `pause` — closing meta card + the summary numbers.

Exact targets are finalized by the probe pass, not assumed here.

## 4. Reliability strategy (zero flake)

A probe harness drives the candidate flow in the rig and reports, per step,
whether the target resolved and the action landed. The loop:

1. Run the flow in the rig; capture per-step success + the resolved target.
2. For any miss, find the **root cause** (systematic-debugging), most likely in:
   - the resolver / skills target matching (wrong or no element),
   - missing **re-capture after a menu opens** (popover items not yet perceived),
   - `reacquire` staleness after the tree changes.
3. Fix the root cause in the tool; add a unit test for the fix (TDD).
4. Re-run until the full flow succeeds **5 consecutive times** with zero misses.

Only then is the flow authored as the final tutorial JSON and recorded. Any tool
fixes are shipped as ordinary tested changes, independent of the video.

## 5. Token instrumentation

Extend the existing tutorial pipeline (no new parallel system):

- **Runner** (`tutorial/runner.py`): for each step, record the **real** estimated
  response tokens via `gateway.budget.estimate_tokens(result)`, and a short
  `perceived` descriptor (element name/role) derived from the step result or a
  follow-up `get_element` of the affected row. Timeline entries gain `tokens` and
  `perceived`; the runner also accumulates run totals.
- **Caption renderer** (`tutorial/captions.py`): compose each on-screen caption
  from `{caption, perceived, tokens, screenshot_equiv}` as a tidy multi-line block,
  and render a **final summary card** (total a11y tokens, total screenshot-
  equivalent, ratio) shown during the closing window.
- **Screenshot-equivalent helper** (`tutorial/tokens.py`): a small pure function converting frame
  dimensions to an image-token estimate using a **documented, cited formula**
  (Anthropic's `tokens ≈ (w·h)/750`; for the rig's 1280×800 ≈ ~1,365 tok/frame),
  reusing/extending `docs/BENCHMARKS.md` methodology. Always labeled "estimate".

All new functions are pure where possible and unit-tested with injected values —
no ffmpeg, no live engine needed for the token/caption math.

## 6. Honest comparison (so it isn't a strawman)

The closing card and the written explainer state plainly:

- The a11y figure is the estimated tokens of the **structured matrix response**
  CUA actually sends (names, roles, actions, geometry) — already actionable.
- The screenshot figure is the estimated **image tokens** a vision model would
  spend to ingest one frame at the same resolution — before it has inferred any
  structure, names, or affordances.
- Both are estimates (heuristic char/token and a published image-token formula),
  labeled as such. We are comparing "tokens to perceive this screen", and we say
  so. We do **not** claim screenshots are useless — the tool itself offers a
  screenshot/vision path for non-accessible UIs.

## 7. Pipeline & deliverables

- Reuse the rig record path (`scripts/record-demo.sh` / `scripts/run-vm.sh` with
  `DEMO=scripts/run-tutorial.py <flow.json>`), then `tutorial.captions.burn_captions`
  to overlay → `mp4`.
- Generate a `gif` (capped frames/size) for the README from the mp4.
- Deliverables:
  - `examples/tutorials/gedit_drive.json` — the verified flow.
  - Token/caption extension in `tutorial/runner.py`, `tutorial/captions.py`, plus a
    screenshot-equivalent helper, all with tests.
  - Any zero-flake fixes to the skills/resolver/reacquire path, with tests.
  - `docs/assets/cua-drive.mp4` + `docs/assets/cua-drive.gif`.
  - Written explainer in `docs/TUTORIALS.md` (+ a README line) describing the
    self-recorded demo and the token tally.

## 8. Verification

- Extract frames from the final mp4 (`ffmpeg`) and visually inspect: captions
  legible and correctly timed, each action visibly performed, numbers correct.
- Confirm the recorded per-step tokens match the runner's timeline for that run.
- Send the gif/mp4 (and a couple of key frames) to the user to eyeball before
  declaring done.

## 9. Components / interfaces touched

| File | Change |
|------|--------|
| `src/cerebellum_cua/tutorial/runner.py` | record per-step `tokens` + `perceived`; accumulate totals |
| `src/cerebellum_cua/tutorial/captions.py` | compose multi-line captions from stats; final summary card |
| `src/cerebellum_cua/tutorial/tokens.py` | pure screenshot-equivalent token estimate |
| `src/cerebellum_cua/skills/*` / resolver / `capture/.../reacquire` | only as zero-flake fixes require (root-caused) |
| `examples/tutorials/gedit_drive.json` | the verified flow |
| `docs/TUTORIALS.md`, `README.md` | explainer + asset |
| `docs/assets/cua-drive.{mp4,gif}` | the artifacts |

## 10. Testing

- TDD units: per-step token recording (runner), caption composition with a stat
  line, summary-card rendering, screenshot-equivalent formula, and every
  zero-flake fix.
- Reliability: the probe harness must show 5 consecutive clean runs in the rig
  before recording (recorded as evidence, not a unit test).
- Full suite + ruff + mypy stay green.

## 11. Non-goals

- No new app added to the rig (gedit only).
- No live-stream (STREAM) mode for this artifact.
- No LLM/model in the loop — CUA is the perception+action layer; the flow is a
  scripted tutorial. (The "agent" is the scripted driver; we do not claim an
  autonomous model is reasoning.)
- No fabricated numbers; no over-claiming in the comparison.
