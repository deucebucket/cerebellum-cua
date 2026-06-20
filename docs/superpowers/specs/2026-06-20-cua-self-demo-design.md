# Spec: CUA self-recorded, captioned, token-annotated demo

Date: 2026-06-20
Status: approved (pending spec review)

## 1. Goal

Produce a short, polished, **continuous-video** screencast in which
cerebellum-cua navigates a real application (gedit) — typing, clicking a toolbar
button, opening the hamburger menu, and selecting a menu item — driven entirely
through the accessibility tree (no screenshots in the perception loop). On-screen
captions show, per step, the element CUA perceived and the **real** token cost of
that step, contrasted with what a screenshot would cost — both a **focused**
(cropped-to-the-element) screenshot and a **full** screenshot. A closing card
gives the **overall total and comparison**. The video is produced by the rig that
runs CUA, and a short written piece explains that the demo drove and recorded
itself.

This work also adds a real capability — **focused (region/element) screenshots**
(§12) — so the comparison is a fair three-way (structured a11y matrix · focused
pixels for one widget · full-screen pixels), not a strawman against a giant image.

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
- A closing card shows the **three-way** total: a11y matrix tokens, focused-shot
  tokens, full-shot tokens, and the ratio.
- The focused-screenshot capability works (crops to a `region`/`row_id` bbox) and
  is covered by tests; full-screen behavior is unchanged when no region is given.
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
6. `op screenshot` with `row_id` — a **focused** screenshot cropped to the element
   just acted on, demonstrating "pixels for one widget" (and its small token cost).
7. `op read_text` — read the screen back, proving perception.
8. `pause` — closing meta card + the summary numbers (three-way total).

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
- **Image-token helper** (`tutorial/tokens.py`): a small pure function converting
  ANY frame dimensions to an image-token estimate using a **documented, cited
  formula** (Anthropic's `tokens ≈ (w·h)/750`; full screen at the rig's 1280×800 ≈
  ~1,365 tok; a focused element crop is far less, scaling with its bbox area),
  reusing/extending `docs/BENCHMARKS.md` methodology. Always labeled "estimate".
  This same helper prices both the focused-shot and full-shot sides of the
  three-way comparison from real bbox/frame dimensions.

All new functions are pure where possible and unit-tested with injected values —
no ffmpeg, no live engine needed for the token/caption math.

## 6. Honest comparison (so it isn't a strawman)

The closing card and the written explainer state plainly:

- The a11y figure is the estimated tokens of the **structured matrix response**
  CUA actually sends (names, roles, actions, geometry) — already actionable.
- The **focused-shot** figure is the estimated image tokens for a screenshot
  cropped to just the element in question — the cheapest *pixel* option, which
  this work makes possible (§12).
- The **full-shot** figure is the estimated image tokens for one whole frame at
  the session resolution — before a vision model has inferred any structure,
  names, or affordances.
- All are estimates (heuristic char/token and a published image-token formula),
  labeled as such. We are comparing "tokens to perceive this screen", and we say
  so. We do **not** claim screenshots are useless — the tool itself offers
  screenshot/focused-screenshot/vision paths for non-accessible UIs; the point is
  that the a11y tree is the cheapest *and* most structured default.

## 7. Pipeline & deliverables

- Reuse the rig record path (`scripts/record-demo.sh` / `scripts/run-vm.sh` with
  `DEMO=scripts/run-tutorial.py <flow.json>`), then `tutorial.captions.burn_captions`
  to overlay → `mp4`.
- Generate a `gif` (capped frames/size) for the README from the mp4.
- Deliverables:
  - **Focused screenshot capability** (§12): `region`/`row_id` on the `screenshot`
    op + MCP tool + per-grabber geometry, with tests.
  - `examples/tutorials/gedit_drive.json` — the verified flow.
  - Token/caption extension in `tutorial/runner.py`, `tutorial/captions.py`, plus the
    `tutorial/tokens.py` image-token helper, all with tests.
  - Any zero-flake fixes to the skills/resolver/reacquire path, with tests.
  - `docs/assets/cua-drive.mp4` + `docs/assets/cua-drive.gif`.
  - Written explainer in `docs/TUTORIALS.md` (+ a README line) describing the
    self-recorded demo and the three-way token tally.

## 8. Verification

- Extract frames from the final mp4 (`ffmpeg`) and visually inspect: captions
  legible and correctly timed, each action visibly performed, numbers correct.
- Confirm the recorded per-step tokens match the runner's timeline for that run.
- Send the gif/mp4 (and a couple of key frames) to the user to eyeball before
  declaring done.

## 9. Components / interfaces touched

| File | Change |
|------|--------|
| `src/cerebellum_cua/capture/screenshot.py` | `region=(x,y,w,h)` crop via per-grabber geometry (ffmpeg/grim/scrot/import) |
| `src/cerebellum_cua/cli/handlers.py` | `screenshot` op accepts `region` or `row_id`(+`snapshot_id`) → bbox lookup → crop |
| `src/cerebellum_cua/mcp/_tools.py` | expose `region`/`row_id` on the `screenshot` MCP tool |
| `src/cerebellum_cua/tutorial/runner.py` | record per-step `tokens` + `perceived`; accumulate totals |
| `src/cerebellum_cua/tutorial/captions.py` | compose multi-line captions from stats; final summary card |
| `src/cerebellum_cua/tutorial/tokens.py` | pure image-token estimate (focused + full) |
| `src/cerebellum_cua/skills/*` / resolver / `capture/.../reacquire` | only as zero-flake fixes require (root-caused) |
| `examples/tutorials/gedit_drive.json` | the verified flow |
| `docs/TUTORIALS.md`, `README.md` | explainer + asset |
| `docs/assets/cua-drive.{mp4,gif}` | the artifacts |

## 10. Testing

- TDD units: focused-screenshot region cropping (per-grabber argv geometry, bbox
  lookup from `row_id`, guarded errors), per-step token recording (runner), caption
  composition with a stat line, summary-card rendering, image-token formula, and
  every zero-flake fix.
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

## 12. Added scope: focused (region/element) screenshots

A real capability added with this work (not just for the demo): grab a screenshot
of **only the region in question** instead of the whole screen — far fewer pixels,
far fewer image tokens, and a natural complement to the a11y tree (which already
knows every element's bounding box).

**API.** The `screenshot` op (and its MCP tool) gains two optional, mutually
exclusive ways to scope the capture:

- `region: [x, y, w, h]` — crop to an explicit rectangle.
- `row_id` (+ optional `snapshot_id`) — look up that element's stored
  `bounding_rect` in the snapshot and crop to it. The common path: `build_matrix`,
  then `screenshot(row_id=N)` for "show me just that widget".

No args → full-screen, exactly as today (backward compatible).

**Cropping.** Done at grab time via each grabber's geometry flags, so the captured
image really is smaller (not a full grab post-cropped):

- ffmpeg x11grab: `-video_size {w}x{h} -i {disp}+{x},{y}`
- grim: `grim -g "{x},{y} {w}x{h}"`
- scrot: `scrot -a {x},{y},{w},{h}`
- ImageMagick import: `import -window root -crop {w}x{h}+{x}+{y}`
- spectacle (Wayland fallback): region capture is interactive/headless-unfriendly —
  fall back to full-screen and note it in the result, never fail silently.

**Result.** Same shape as today (`{path, width, height}`), where width/height now
reflect the cropped image, plus an echoed `region` so the caller knows what was
captured. Out-of-bounds / zero-area regions are clamped to the frame or raise a
typed error — never produce a corrupt grab.

**Why it matters here.** It lets the demo show a fair three-way token comparison
(matrix vs focused shot vs full shot) and gives agents a cheap "look closely at
this one thing" option that pairs with the a11y tree instead of competing with it.
