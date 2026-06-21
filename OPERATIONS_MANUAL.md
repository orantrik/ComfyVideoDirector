# ComfyVideoDirector — Operations Manual

**ArchViz → Branded Video: Scene Identity-Lock Pipeline**

---

## What this system does

You drop in Unreal Engine screenshots (one per scene), and the pipeline produces a fully
branded video film: per-scene lip-sync video clips of your spokesman walking through the
space, composited into a final `.mp4`. Everything — furniture identity, character
consistency, voiceover, video — is automated.

```
Unreal screenshots  →  Identity Container  →  Hero Images  →  Voiceover  →  Scene Clips  →  Final Film
```

---

## Prerequisites checklist

### 1. ComfyUI

- ComfyUI must be **running** during a live production run.
- Start it from its folder: `python main.py --port 8001`
- Default API port: `http://127.0.0.1:8001`

### 2. Required models (place in the indicated ComfyUI subfolders)

| Model file | Folder |
|---|---|
| `ltx-2-3-22b-dev_transformer_only_fp8_input_scaled.safetensors` | `models/unet/` |
| `ltx-2.3-22b-distilled-lora-384.safetensors` | `models/loras/` |
| `ltx-2-19b-lora-camera-control-static.safetensors` | `models/loras/` |
| `ltxv-098-ic-lora-detailer-comfyui.safetensors` | `models/loras/` |
| `LTX23_video_vae_bf16.safetensors` | `models/vae/` |
| `LTX23_audio_vae_bf16.safetensors` | `models/vae/` |
| `gemma_3_12B_it_fp8_e4m3fn.safetensors` | `models/text_encoders/` |
| `ltx-2.3_text_projection_bf16.safetensors` | `models/text_encoders/` |
| `ltx-2.3-spatial-upscaler-x2-1.0.safetensors` | `models/latent_upscale_models/` |
| `MelBandRoformer_fp16.safetensors` | `models/diffusion_models/` |

Download links for LTX models: https://huggingface.co/Lightricks/LTX-2.3  
Download link for MelBandRoformer: https://huggingface.co/Kijai/MelBandRoFormer_comfy

### 3. Required ComfyUI custom nodes (already installed)

Located in `ComfyUI/custom_nodes/`:

| Node pack | Purpose |
|---|---|
| `comfyui-ai-director` | This orchestrator + all custom nodes |
| `ComfyUI-F5-TTS` | Free voice-cloning TTS |
| `comfyui-kokoro` | Free preset-voice TTS (fallback) |
| `comfyui-kjnodes` | KJ utility nodes (VAELoaderKJ, etc.) |
| `ComfyUI-LTXVideo` | LTX tiled VAE decode |
| `ComfyUI-MelBandRoFormer` | Audio stem separation |
| `RES4LYF` | Clown sampler nodes |
| `comfyui-easy-use` | Utility nodes |

### 4. ffmpeg

Required for Phase 5 (final compose). Install it so it is on your PATH:

```
winget install ffmpeg
```

Or install `imageio-ffmpeg` inside the ComfyUI venv as a fallback:

```
C:\Users\oranbenshaprut\Documents\ComfyUI\venv\Scripts\pip.exe install imageio-ffmpeg
```

### 5. Validate the LipSync recipe (one-time, first run only)

Open ComfyUI in your browser → drag in
`comfyui-ai-director/recipes/LipSync_AutoTour_ORIGINAL_DO_NOT_EDIT.json` → confirm it
loads with no red nodes. This checks that all required models and nodes are present.

---

## Folder layout you set up

```
my_project/                          ← your project root (you create this)
  cast/
    spokesman/
      voice_ref.wav                  ← (optional) 5–15 sec clear speech clip for voice cloning
      voice_ref.txt                  ← (optional) exact transcript of what is said in voice_ref.wav
  scenes/                            ← auto-created by the pipeline
    ...

screenshots/                         ← your Unreal screenshots folder
  scene_01.png
  scene_02.png
  ...                                ← one PNG per scene, named so they sort in scene order

cast.json                            ← (optional) custom cast description — see template below
```

---

## cast.json template

If you do not supply `--cast`, a built-in default spokesman is used. To customise:

```json
{
  "spokesman": {
    "desc": "A woman in her early 30s, golden-brown wavy hair, tailored cream trench coat, warm confident presence",
    "clothes": [
      "cream tailored trench coat",
      "navy fitted blazer"
    ]
  },
  "actors": [
    {
      "id": "a01",
      "desc": "A man in his 40s, short dark hair, navy suit, calm professional demeanour"
    }
  ]
}
```

---

## Voice reference (for voice cloning)

Place these two files inside your project **before** you run Phase 3:

```
my_project/cast/spokesman/voice_ref.wav   ← 5–15 seconds of clear speech, no music
my_project/cast/spokesman/voice_ref.txt   ← exact words spoken in that clip
```

If these files are absent the pipeline falls back to Kokoro TTS with a preset voice
(set via `--kokoro-voice`).

---

## Running the pipeline

### Quick start — dry-run (offline, no ComfyUI needed)

Validates the full pipeline logic with placeholder images. Use this first.

```bash
cd C:\Users\oranbenshaprut\Documents\ComfyUI\custom_nodes\comfyui-ai-director

python archviz_director.py \
  --project  C:\path\to\my_project \
  --frames   C:\path\to\screenshots \
  --dry-run
```

Expected output ends with:

```
DONE. Identity container at: C:\path\to\my_project
Index: C:\path\to\my_project\identity_index.json
```

---

### Live run — all 5 phases (recommended for production)

```bash
python archviz_director.py \
  --project    C:\path\to\my_project \
  --frames     C:\path\to\screenshots \
  --comfy-url  http://127.0.0.1:8001 \
  --recipes    C:\Users\oranbenshaprut\Documents\ComfyUI\custom_nodes\comfyui-ai-director\recipes \
  --cast       C:\path\to\cast.json \
  --phases     1,2,3,4,5
```

This runs all scenes end-to-end and produces `my_project/final_film.mp4`.

---

### Running individual phases

You can run any subset of phases. Useful for re-running a single stage without repeating
expensive generations.

```bash
# Phase 1 only — identity extraction + packshots
--phases 1

# Phase 1 + 2 — identity + hero assembly
--phases 1,2

# Phase 3 only — regenerate voiceover (e.g. after updating voice_ref.wav)
--phases 3

# Phase 4 only — regenerate video clips (hero_locked.png + voiceover already exist)
--phases 4

# Phase 5 only — re-compose the final film from already-generated clips
--phases 5

# Full production run
--phases 1,2,3,4,5
```

---

### Single scene

```bash
--scene 2    # process only scene 2 (uses the second file in --frames, sorted)
```

---

## All command-line options

| Flag | Default | Description |
|---|---|---|
| `--project` | required | Project root folder |
| `--frames` | required | Folder of Unreal screenshots (sorted by filename) |
| `--comfy-url` | `http://127.0.0.1:8001` | ComfyUI API URL |
| `--recipes` | *(dry-run if omitted)* | Folder containing the API-format recipe JSON files |
| `--cast` | *(built-in default)* | Path to your `cast.json` |
| `--scene` | `0` (all) | Process only one scene by number |
| `--phases` | `1,2,3,4,5` | Comma-separated list of phases to run |
| `--dry-run` | off | Offline mode — placeholder images, no ComfyUI needed |
| `--kokoro-voice` | `af_heart` | Kokoro TTS preset (used when no `voice_ref.wav` present) |
| `--compose-mode` | `hard_cut_reencode` | ffmpeg assembly: `hard_cut_reencode`, `hard_cut_copy`, `crossfade` |
| `--crossfade-secs` | `0.5` | Dissolve length in seconds (crossfade mode only) |
| `--clip-duration` | `8` | Expected clip duration in seconds (crossfade timing) |
| `--output-film` | `<project>/final_film.mp4` | Override path for the final composed film |

---

## What each phase does

### Phase 1 — Identity front-end (Stages A–F)

For each scene:
- **A** Ingest: copies the screenshot → `scene_XX/source.png`
- **B** Analysis: Qwen3-VL reads the screenshot → detailed furniture list, object list, space geometry
- **C** Packshots: NanoBanana generates a 4-view white-background packshot for every furniture piece and object
- **D** Stabilized space: NanoBanana generates a clean empty-space hero conditioned on all packshots
- **E** Cast: generates 7-angle character sheet + portrait + clothes packshots for spokesman and all actors
- **F** Container write: everything is registered in `identity_index.json`

**Output**: a fully populated `identity/` folder per scene.

### Phase 2 — Scene assembly (Stages G, I, J, K, L)

- **G** Coordinates + master prompt: Qwen3-VL maps every element to x/y positions; writes a master prompt describing the start frame of the next scene (cross-scene continuity)
- **I** Hero composite: NanoBanana generates 4 variations of the full hero image (empty space + spokesman + actors + all props in position)
- **J** Prompt reconciliation: Qwen3-VL rewrites the master prompt so every action references a real, identified element by name and location; `object_lock.py` adds anti-hallucination guardrails
- **K** Inspector gate: Qwen3-VL scores every element in each hero variation vs its packshot (threshold ≥ 90%); picks the best passing variation
- **L** Lock hero: copies the winning variation → `renders/hero_locked.png`

**Output**: `hero_locked.png`, `prompts/positive.txt`, `prompts/negative.txt`, `report.json`

### Phase 3 — Audio (Stage H)

- Reads `prompts/audio_prompt.txt`
- If `cast/spokesman/voice_ref.wav` exists → F5-TTS voice cloning
- Otherwise → Kokoro TTS with `--kokoro-voice` preset
- Writes `audio/voiceover.wav`, registered in `identity_index.json`

**Output**: `audio/voiceover.wav`

### Phase 4 — Video (Stage M)

- Feeds `hero_locked.png` + `audio/voiceover.wav` + reconciled prompts into the LTX-2 lip-sync pipeline
- Generates `renders/scene_video.mp4`
- Registered in `identity_index.json`

**Output**: `renders/scene_video.mp4` per scene

### Phase 5 — Compose final film

- Collects every `scene_XX/renders/scene_video.mp4` in scene order
- Concatenates them via ffmpeg
- Writes `final_film.mp4` and `compose_manifest.json`

**Output**: `my_project/final_film.mp4`

---

## What the project folder looks like after a full run

```
my_project/
  identity_index.json          ← master registry of all identities
  compose_manifest.json        ← list of clips + settings used
  final_film.mp4               ← the finished branded video
  cast/
    spokesman/
      desc.txt
      sheet_7angle.png
      portrait.png
      voice_ref.wav            ← your file (if voice cloning)
      voice_ref.txt
      clothes_packshots/
        garment_01.png
  scenes/
    scene_01/
      source.png               ← original screenshot
      coordinates.json
      report.json              ← inspector scores per element
      identity/
        space.json
        space_map.png
        furniture/
          f01/desc.txt, packshot_4view.png
          f02/desc.txt, packshot_4view.png
        objects/
          o01/desc.txt, packshot_4view.png
      prompts/
        master_prompt.txt
        audio_prompt.txt
        positive.txt
        negative.txt
      audio/
        voiceover.wav
      renders/
        empty_space_stabilized.png
        hero_v1.png  hero_v2.png  hero_v3.png  hero_v4.png
        hero_locked.png
        scene_video.mp4
    scene_02/
      ...
```

---

## Kokoro TTS voice presets

Good English voices for `--kokoro-voice`:

| Preset | Style |
|---|---|
| `af_heart` | Warm American female (default) |
| `af_bella` | Calm American female |
| `af_nova` | Bright American female |
| `bf_emma` | British female |
| `bf_alice` | Crisp British female |

---

## Common workflows

### "I want to re-record the voiceover with a better voice clip"

1. Replace `my_project/cast/spokesman/voice_ref.wav` and `voice_ref.txt`
2. Re-run Phase 3:
   ```bash
   python archviz_director.py --project my_project --frames screenshots --phases 3 \
     --comfy-url http://127.0.0.1:8001 --recipes ./recipes
   ```
3. Re-run Phase 4 (new audio → new video):
   ```bash
   python archviz_director.py --project my_project --frames screenshots --phases 4 \
     --comfy-url http://127.0.0.1:8001 --recipes ./recipes
   ```
4. Re-compose:
   ```bash
   python archviz_director.py --project my_project --frames screenshots --phases 5
   ```

### "The inspector failed for one scene — I want to regenerate just that scene's hero"

```bash
python archviz_director.py --project my_project --frames screenshots \
  --scene 3 --phases 2 \
  --comfy-url http://127.0.0.1:8001 --recipes ./recipes
```

Then re-run Phases 3, 4 for that scene and re-compose with Phase 5.

### "I want a crossfade between scenes"

```bash
python archviz_director.py --project my_project --frames screenshots --phases 5 \
  --compose-mode crossfade --crossfade-secs 0.75 --clip-duration 8
```

### "I want to add a new scene to an existing project"

1. Add the new screenshot to your `screenshots/` folder (it will sort after existing ones)
2. Run all phases on the new scene only:
   ```bash
   python archviz_director.py --project my_project --frames screenshots \
     --scene 7 --phases 1,2,3,4 \
     --comfy-url http://127.0.0.1:8001 --recipes ./recipes
   ```
3. Re-compose with Phase 5

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `no screenshots found` | Check that `--frames` points to a folder containing `.png`/`.jpg` files |
| `recipe has no node id X` | The recipe JSON does not match ComfyUI's node IDs — re-export `lipsync_scene.json` from ComfyUI via Workflow → Export (API Format) |
| `video recipe produced no output` | Check ComfyUI console for errors; usually a missing model file |
| Inspector scores < 90% on every variation | Increase the number of variations (edit `stage_hero_composite` loop range 1–4 to 1–8) or add more reference packshots |
| `ffmpeg not found` — Phase 5 writes `.txt` instead of `.mp4` | Install ffmpeg: `winget install ffmpeg` then restart the terminal |
| Voice sounds robotic (Kokoro mode) | Add a `voice_ref.wav` + `voice_ref.txt` to enable F5-TTS voice cloning |
| ComfyUI queue times out | Increase `timeout` in `run_recipe_video()` in `core/comfy_api.py` (default 3600 s) |

---

## Repository

**GitHub**: https://github.com/orantrik/ComfyVideoDirector

All source code, recipes, and this manual live in:
```
C:\Users\oranbenshaprut\Documents\ComfyUI\custom_nodes\comfyui-ai-director\
```
