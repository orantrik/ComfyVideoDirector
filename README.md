# ComfyUI AI Director (v2 — agnostic one-shot)

Turns **any ordered set of images** (any subject) into a single continuous
video, built from one 8-second clip per consecutive frame pair, where **each
clip's end frame is the next clip's start frame** — a seamless one-shot. It
reads the frames (wire Qwen3-VL captions), writes the per-clip motion prompt,
feeds the video graph directly, and **binds the clips into the full scene**
with ffmpeg.

## Two modes
- **`keyframe_pairs` (agnostic, default)** — N frames → N-1 clips. No categories
  required. Clip i goes frame[i] → frame[i+1]; end of i = start of i+1.
- **`fixed_grid` (legacy)** — fixed duration / 8s grid with architectural
  category validation (the original tour planner).

## "Understands the images"
Wire your **Qwen3-VL (`SimpleQwenVLgguf`)** to caption each frame and feed the
captions into Frame Intake's **`frame_descriptions`** (one line per frame, in
order). The Director turns consecutive captions into the motion between them
("camera moves from X to Y"). You can also type captions, or add per-transition
camera notes in the Shot Planner's **`motion_notes`**.

## Ready-to-load workflow
`ClaudeVideoGen_AIDirector.json` (in Downloads) is your LTXV graph with the full
Director chain injected and wired: Picker → 304 (positive), 315 (negative), 372
(save name), and 346 (start image via **Load Frame**). Set the frames folder,
then step `segment_index` 1..N-1, queueing each clip.

> v1 = the reliable **director manifest + prompts** and the **assembler**.
> The drag-and-drop thumbnail GUI is a planned phase 2. The nodes are fully
> usable now via a folder of ordered screenshots.

## Nodes (category **AI Director**)

| Node | Job |
|------|-----|
| Project Setup | Global config; computes segment count; emits the frame-request text. |
| Unreal Frame Intake | Turns selected files / a folder into ordered `PATH_FRAMES`. |
| Frame Classifier | Assigns each frame a camera-path category (filename rules + GUI override). |
| Continuous Path Validator | Flags missing required categories and implausible space jumps. |
| 30 Segment Planner | Builds the N-segment timeline, maps keyframes, marks ready/blocked. |
| Prompt Compiler | One continuity-aware video prompt per segment. |
| Manifest Exporter | Writes JSON + TXT manifest/prompts/report. |
| **Video Assembler** | ffmpeg-concatenates the rendered clips into the final film. |

## Pipeline wiring

```
Project Setup ─project─┬─────────────────────────────┐
                       ▼                             │
Unreal Frame Intake ─frames─► Frame Classifier ─classified─┐
                                                          ▼
                              Continuous Path Validator ─report─┐
                                                          ▼     │
                              30 Segment Planner ◄──project/classified/report
                                       │ segments
                                       ▼
                              Prompt Compiler ─manifest─► Manifest Exporter
                                                                  │
   (render each segment's prompt with your image→video flow into one folder)
                                                                  ▼
                                       Video Assembler (clips_folder) ─► final_commercial.mp4
```

## Using it now (pre-GUI)
1. **Project Setup** — set duration (e.g. 240) and segment length (8).
2. **Unreal Frame Intake** — set `selected_frames_folder` to a folder of
   screenshots named `001_...`, `002_...`, or paste a newline list of paths in
   `selected_frame_files`.
3. **Frame Classifier** → **Path Validator** → **Segment Planner** →
   **Prompt Compiler** → **Manifest Exporter**.
4. Render each segment prompt with your image→video flow, saving clips into one
   folder (sortable names, e.g. `seg_01.mp4`).
5. **Video Assembler** — point `clips_folder` at that folder, pick a mode,
   get `final_commercial.mp4`.

### Assembler modes
- `hard_cut_copy` — fastest, stream-copy concat (needs uniform codec/fps).
- `hard_cut_reencode` — safe default; re-encodes to H.264.
- `crossfade` — smooth dissolves between clips (assumes uniform clip length).

## Data types passed between nodes
Plain JSON-friendly dicts/lists (`DIRECTOR_PROJECT`, `PATH_FRAMES`,
`CLASSIFIED_PATH_FRAMES`, `MISSING_FRAME_REPORT`, `CONTINUOUS_SEGMENTS`,
`PROMPT_MANIFEST`) — everything is exportable and inspectable.

## Fully automated batch runner (`ai_director_run.py`)
Renders the whole scene with one command, feeding each clip's generated LAST
frame forward as the next clip's START frame. Runs OUTSIDE ComfyUI, driving it
over the API.

1. In ComfyUI, open your video graph and **Workflow -> Export (API)** → save as
   e.g. `ClaudeVideoGen_api.json`.
2. With ComfyUI running:
```
python ai_director_run.py \
  --frames "C:/frames" \
  --captions "C:/frames/captions.txt" \
  --workflow-api "C:/.../ClaudeVideoGen_api.json" \
  --output-dir "C:/.../ComfyUI/output"
```
- `--dry-run` plans + prints prompts without touching ComfyUI/ffmpeg.
- Node-id map defaults to ClaudeVideoGen.json (304 positive, 315 negative,
  349 start image, 372 save); override with `--positive-node` etc. if needed.
- Per clip it: uploads the start frame (`/upload/image`), patches prompt +
  negative + start image + save name, queues (`/prompt`), waits (`/history`),
  finds the output, extracts the last frame (ffmpeg), feeds it to the next clip,
  then concatenates all clips into `final_commercial.mp4`.

> Note: your current LTXV graph is single-start-image (no end-keyframe input),
> so the runner anchors the END via the text prompt (which describes the next
> keyframe). To get true pixel-level end anchoring, add LTXV end-keyframe
> conditioning and feed the Picker's `end_image_path` into it.

## Phase 2 (not yet built)
GUI thumbnail grid, drag-reorder, category dropdowns, coverage checklist,
segment timeline viewer, and folder/save pickers (per the spec's GUI section).
