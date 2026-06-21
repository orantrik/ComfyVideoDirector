#!/usr/bin/env python3
"""
ArchViz Scene Director - the orchestrator.
==========================================

Drives the per-scene identity-lock pipeline. ComfyUI is the render engine
(NanoBanana image gen, Qwen3-VL analysis); this controller loops scenes and
variable item counts, fills the identity container, and (later phases) runs the
inspector gates + reporter and the video stage.

Run offline to scaffold + validate the whole container with placeholders:
    python archviz_director.py --project ./proj --frames ./screenshots --dry-run

Live (needs ComfyUI up + API-format recipe graphs in --recipes):
    python archviz_director.py --project ./proj --frames ./screenshots \
        --comfy-url http://127.0.0.1:8001 --recipes ./recipes --cast cast.json
"""

import os
import re
import sys
import glob
import json
import shutil
import struct
import base64
import argparse

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PKG_DIR)
from core import identity as ID            # noqa: E402
from core import prompts_archviz as P      # noqa: E402
from core.asset_cache import AssetCache    # noqa: E402
from core.token_meter import TokenMeter    # noqa: E402

METER = TokenMeter()   # reset in main(); always available for stage functions

# Quality-gated reuse cache. Initialised in main(); a disabled stand-in is used
# for direct imports / tests so the stages always have a CACHE to call.
CACHE = AssetCache(os.getcwd(), enabled=False)


def _ensure(root, out_path, kind, gen_fn, min_score=None):
    """Generate out_path via gen_fn() unless a good existing asset can be reused.

    This is the single choke point for credit-spending image generation: if the
    asset already exists on disk and passes the quality gate, we skip the paid
    call entirely and reuse it.
    """
    if CACHE.should_skip(out_path, kind=kind, min_score=min_score):
        print(f"    [reuse] {os.path.relpath(out_path, root)} (passed quality gate)",
              flush=True)
        return out_path
    gen_fn()
    CACHE.record(out_path, kind=kind)
    return out_path

# 64x64 white PNG used as a placeholder asset in dry-run. A 1x1 image makes some
# LoadImage builds crash inside PyAV, so we keep it comfortably sized + valid.
_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAfElEQVR4nNXOQREAIADDsFL/no"
    "cIHlyjIGcbZRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIn"
    "cRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncf4OvLpyqgN9ZS"
    "iDcwAAAABJRU5ErkJggg=="
)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# --------------------------------------------------------------------------- #
#  Cost controls — every NanoBanana / Gemini call costs API credits, so cap the
#  expensive per-element work. Tuned in main() from CLI flags.
#    max_packshots : max number of element packshots to generate per scene
#    inspect_mode  : "off"  -> skip QA scoring (0 Gemini calls; pick hero_v1)
#                    "fast" -> 1 holistic Gemini call per hero variation
#                    "full" -> 1 Gemini call per element per variation (most $$)
#    hero_variations : how many hero composites to generate (each = 1 NanoBanana)
#    skip_clutter  : in exterior mode, don't packshot trivial site clutter
# --------------------------------------------------------------------------- #
LIMITS = {
    "max_packshots":   8,
    "inspect_mode":    "fast",
    "hero_variations": 4,
    "skip_clutter":    True,
    # Stage P: ultra-photoreal locked image via Nano Banana Pro.
    "photoreal":          True,
    "hero_resolution":    "2K",   # 1K | 2K | 4K
    "photoreal_min":      80,     # realism score (0-100) required to stop
    "photoreal_attempts": 3,      # max Pro re-renders per scene
}

# Names/keywords that aren't worth a dedicated identity packshot (exterior clutter).
_CLUTTER_KEYWORDS = (
    "parking", "lot", "asphalt", "pavement", "road", "street", "kerb", "curb",
    "sidewalk", "lane marking", "white line", "painted line", "sky", "cloud",
    "ground", "grass", "lawn", "planter", "shrub", "bush", "hedge", "distant",
    "background", "skyline", "smokestack", "industrial plant", "horizon",
)


def _is_clutter(item):
    blob = f"{item.get('name', '')} {item.get('desc', '')} {item.get('location', '')}".lower()
    return any(k in blob for k in _CLUTTER_KEYWORDS)


def _usable_ref(path):
    """True if path is a readable image > 1 KB. Guards against stale/corrupt
    placeholders (e.g. dry-run leftovers) crashing a live generation."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 1024:
            return False
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return True
    except ImportError:
        # PIL unavailable: fall back to a size check only.
        return os.path.isfile(path) and os.path.getsize(path) >= 1024
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Clients (same interface; pick dry-run or live)
# --------------------------------------------------------------------------- #
class DryRunClient:
    label = "dry-run"

    def analyze(self, user_prompt, image_path, system_prompt=""):
        # Canned, structurally-valid responses so every pipeline stage exercises fully.
        # Order matters: more specific checks first.
        # --- EXTERIOR ArchViz dry-run responses ---
        if "BUILT or STRUCTURAL element" in user_prompt:
            return ("f01 | north tower | slim glass-and-concrete high-rise, vertical "
                    "balcony bands, ~30 storeys | centre-left, foreground\n"
                    "f02 | south tower | matching glass tower, stepped crown | "
                    "centre-right, slightly behind north tower\n"
                    "f03 | podium | 3-storey stone-clad base linking both towers | "
                    "ground level, spanning the scene")
        if "secondary SITE element" in user_prompt:
            return ("o01 | entrance canopy | brushed-steel and glass | base of north "
                    "tower, centre\n"
                    "o02 | mature trees | row of green deciduous trees | along the "
                    "forecourt, foreground\n"
                    "o03 | parked car | dark grey sedan | kerbside, lower-left")
        if "EXTERIOR scene: overall site composition" in user_prompt:
            return ("Two slender residential towers on a shared stone podium, modern "
                    "glass architecture, early-evening sky. Zones: tower cluster "
                    "(centre), podium/retail (ground), landscaped forecourt "
                    "(foreground), street/approach (lower edge), city skyline "
                    "(background).")
        if "every small OBJECT" in user_prompt:
            return ("o01 | short whisky glass | clear cut crystal | on the coffee table, right side\n"
                    "o02 | ceramic vase | matte sage green | on the side table, left of sofa")
        if "piece of FURNITURE" in user_prompt:
            return ("f01 | grey 3-seat sofa | charcoal boucle fabric, low oak legs | "
                    "centre of lounge, facing the window wall\n"
                    "f02 | walnut coffee table | round walnut top, black metal base | "
                    "in front of the sofa")
        if "For EVERY visible element" in user_prompt:
            # Stage G coordinates — full element list with x/y positions.
            return json.dumps([
                {"id": "f01", "label": "sofa",         "kind": "furniture", "x": 0.50, "y": 0.60, "area": "lounge"},
                {"id": "f02", "label": "coffee table",  "kind": "furniture", "x": 0.50, "y": 0.72, "area": "lounge"},
                {"id": "o01", "label": "whisky glass",  "kind": "object",    "x": 0.55, "y": 0.74, "area": "lounge"},
                {"id": "o02", "label": "ceramic vase",  "kind": "object",    "x": 0.30, "y": 0.65, "area": "lounge"},
                {"id": "spokesman", "label": "spokesman", "kind": "person",  "x": 0.40, "y": 0.50, "area": "lounge"},
                {"id": "a01",       "label": "actor a01", "kind": "person",  "x": 0.65, "y": 0.55, "area": "lounge"},
            ])
        if "photorealism judge" in user_prompt or "indistinguishable from a REAL" in user_prompt:
            # Stage P realism inspector — pass on the first try in dry-run.
            return json.dumps({"score": 92, "problems": []})
        if "QA inspector" in user_prompt or "Compare the GENERATED" in user_prompt:
            # Stage K inspector — extract the label from the prompt.
            m = re.search(r"for '([^']+)'", user_prompt)
            label = m.group(1) if m else "element"
            return json.dumps({"label": label, "score": 95,
                               "hallucinations": [], "notes": "matches reference (dry-run)"})
        if "Write a single cinematic master prompt" in user_prompt:
            return ("A wide cinematic shot of the open-plan lounge bathed in warm afternoon light. "
                    "The spokesman stands near the walnut coffee table, the whisky glass resting to "
                    "her right. The space feels calm, intentional, curated.")
        if "voiceover narration" in user_prompt.lower():
            return ("She moves through the space with quiet confidence, each detail placed with "
                    "intention. The room speaks before anyone does.")
        if "continuity supervisor" in user_prompt or "Return only the rewritten prompt" in user_prompt:
            return ("The spokesman lifts the short crystal whisky glass from the right side of the "
                    "walnut coffee table and brings it to her lips, her gaze resting on the window wall.")
        if "spatial" in user_prompt or "space" in user_prompt.lower():
            return ("Open-plan lounge, ~6.0 x 5.0 x 2.8 m. Zones: lounge area (centre), "
                    "window wall (far side, full-height glazing), entry (near-left).")
        return "OK"

    def generate(self, prompt, ref_paths, out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as fh:
            fh.write(_PLACEHOLDER_PNG)
        return out_path

    def generate_pro(self, prompt, ref_paths, out_path, resolution="2K",
                     aspect="16:9", attempts=3):
        return self.generate(prompt, ref_paths, out_path)

    def generate_audio(self, text, ref_audio_path, out_path, voice="af_heart"):
        """Write a minimal silent WAV so the pipeline can exercise Stage H offline."""
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        # 44-byte WAV header + 4 bytes silence (2 samples, 16-bit, 22050 Hz, mono).
        data = b"\x00\x00\x00\x00"
        header = struct.pack("<4sI4s4sIHHIIHH4sI",
                             b"RIFF", 36 + len(data), b"WAVE",
                             b"fmt ", 16, 1, 1, 22050, 44100, 2, 16,
                             b"data", len(data))
        with open(out_path, "wb") as fh:
            fh.write(header + data)
        engine = "f5-tts" if ref_audio_path else "kokoro"
        print(f"    [dry-run audio] {engine} -> {os.path.basename(out_path)}")
        return out_path

    def generate_video(self, positive, negative, hero_path, voiceover_path,
                       duration_secs, out_path, prefix="scene"):
        """Write a 4-byte placeholder .mp4 so Stage M exercises offline."""
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as fh:
            fh.write(b"DRY\n")   # smallest possible sentinel
        print(f"    [dry-run video] LTX-2 lip-sync -> {os.path.basename(out_path)}")
        return out_path

    def generate_director_video(self, frames_folder, segment_index, out_dir):
        """Dry-run placeholder for the director video stage."""
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "scene_video.mp4")
        with open(out, "wb") as fh:
            fh.write(b"DRY\n")
        print(f"    [dry-run director] segment {segment_index} -> {os.path.basename(out)}")
        return out


class ComfyClient:
    label = "comfyui"

    def __init__(self, comfy_url, recipes_dir):
        from core import comfy_api
        self.api = comfy_api
        self.url = comfy_url
        self.recipes = recipes_dir
        # Expected recipe files + the node ids the controller patches.
        self.qwen_recipe    = os.path.join(recipes_dir, "qwen_analyze.json")
        self.gemini_analyze_recipe = os.path.join(recipes_dir, "gemini_analyze.json")
        self.nb_recipe      = os.path.join(recipes_dir, "nanobanana_gen.json")
        self.f5_recipe      = os.path.join(recipes_dir, "f5_tts.json")
        self.kokoro_recipe  = os.path.join(recipes_dir, "kokoro_tts.json")
        self.lipsync_recipe  = os.path.join(recipes_dir, "lipsync_scene.json")
        # Director recipe: the converted ClaudeVideoGen_AIDirector.json workflow.
        # Activates when --director-frames is supplied.
        self.director_recipe = os.path.join(recipes_dir, "ltx_director_scene.json")
        # Node-id maps — match qwen_analyze.json and nanobanana_gen.json layouts:
        #   qwen_analyze.json:   "1"=LoadImage  "2"=SimpleQwenVLgguf  "3"=SaveText
        #   nanobanana_gen.json: "1"=LoadImage(ref1) "2"=LoadImage(ref2)
        #                        "3"=GeminiNanoBanana2V2  "4"=SaveImage
        self.qwen_nodes   = {"image": "1", "user_prompt": "2", "text_out": "3"}
        #   gemini_analyze.json: "1"=LoadImage "2"=GeminiNode "3"=SaveText
        self.gemini_analyze_nodes = {"image": "1", "gemini": "2", "text_out": "3"}
        # Vision backend for Stages B/G/J/K. The local Qwen3-VL GGUF path needs a
        # newer llama-cpp-python (with Qwen3VLChatHandler); when that's missing it
        # fails every inference, so the default is the reliable Gemini API node.
        self.vision_backend = "gemini"          # "gemini" | "qwen"
        self.vision_model   = "gemini-3-1-pro"  # GeminiNode model id
        self.nb_nodes     = {"prompt": "3", "image_1": "1", "image_2": "2", "save": "4"}
        # API-node auth + model selection (set from the GUI / CLI).
        #   api_token   -> sent as extra_data.api_key_comfy_org so Gemini/NanoBanana
        #                  can authenticate without a logged-in ComfyOrg session.
        #   image_model -> patched into the GeminiNanoBanana2V2 'model' input.
        self.api_token   = ""
        self.image_model = "Nano Banana 2 (Gemini 3.1 Flash Image)"
        # Ultra-photoreal locked-image model (GeminiImage2Node). gemini-3-pro-image
        # = "Nano Banana Pro": slower + pricier, used only for the final hero.
        self.hero_model  = "gemini-3-pro-image-preview"
        # Qwen model paths — set from CLI or defaults from ClaudeImageGen.json workflow
        _llm = r"C:\Users\oranbenshaprut\Documents\ComfyUI\models\LLM"
        self.qwen_model_path  = os.path.join(_llm, "Qwen3VL-8B-Instruct-Q8_0.gguf")
        self.qwen_mmproj_path = os.path.join(_llm, "mmproj-Qwen3VL-8B-Instruct-F16.gguf")
        # F5-TTS node IDs — match f5_tts.json layout:
        #   "1"=LoadAudio, "2"=F5TTSAudioInputs, "3"=SaveAudio
        self.f5_nodes     = {"ref_audio": "1", "text": "2", "save": "3"}
        # Kokoro node IDs — match kokoro_tts.json layout:
        #   "1"=KokoroSpeaker, "2"=KokoroGenerator, "3"=SaveAudio
        self.kokoro_nodes = {"voice_node": "1", "text_node": "2", "save": "3"}
        self.kokoro_voice = "af_heart"   # change to preferred Kokoro voice preset
        # LipSync / LTX-2 node IDs — match lipsync_scene.json layout:
        #   "304"=CLIPTextEncode positive  "315"=CLIPTextEncode negative
        #   "436"=AIDirectorLoadFrame      "414"=LoadAudio
        #   "418"=TrimAudioDuration        "273"=SaveVideo
        self.lipsync_nodes = {
            "positive":  "304",
            "negative":  "315",
            "image":     "436",
            "audio":     "414",
            "trim":      "418",
            "save":      "273",
        }

    def _extra(self):
        """extra_data for /prompt so API nodes can authenticate. None if no token."""
        return {"api_key_comfy_org": self.api_token} if self.api_token else None

    def analyze(self, user_prompt, image_path, system_prompt=""):
        import random as _rng
        up = self.api.upload_image(self.url, image_path)
        if self.vision_backend == "qwen":
            n = self.qwen_nodes
            qwen_patch = {
                "user_prompt":  user_prompt,
                "model_path":   self.qwen_model_path,
                "mmproj_path":  self.qwen_mmproj_path,
            }
            if system_prompt:
                qwen_patch["system_prompt"] = system_prompt
            patches = {
                n["image"]:      {"image": up},
                n["user_prompt"]: qwen_patch,
            }
            return self.api.run_recipe_text(self.url, self.qwen_recipe, patches,
                                            text_node=n["text_out"], extra_data=self._extra())
        # Default: Gemini API vision node (reliable; uses the same API key).
        n = self.gemini_analyze_nodes
        gpatch = {"prompt": user_prompt, "seed": _rng.randint(0, 2 ** 31)}
        if self.vision_model:
            gpatch["model"] = self.vision_model
        if system_prompt:
            gpatch["system_prompt"] = system_prompt
        patches = {
            n["image"]:  {"image": up},
            n["gemini"]: gpatch,
        }
        result = self.api.run_recipe_text(self.url, self.gemini_analyze_recipe, patches,
                                          text_node=n["text_out"], extra_data=self._extra())
        METER.add_analyze(user_prompt, result)
        return result

    MAX_REFS = 14   # GeminiNanoBanana2V2 accepts up to 14 reference images

    def generate(self, prompt, ref_paths, out_path, attempts=3):
        """Generate one image, wiring EVERY reference image into NanoBanana.

        ref_paths[0] is treated as the ground-truth image (the real room
        screenshot for space/hero stages) and becomes reference image_1; the
        prompts instruct the model to keep that space identical. Remaining refs
        (packshots, character sheets) lock object/person identity.

        Gemini occasionally returns reasoning text instead of an image, so we
        retry with a fresh seed (and request IMAGE+TEXT on the last try).
        """
        import json as _json
        import copy as _copy
        import random as _rng

        prompt_node = self.nb_nodes["prompt"]
        base = _json.load(open(self.nb_recipe, encoding="utf-8"))
        base = {k: v for k, v in base.items() if not k.startswith("_")}

        # Strip the recipe's hard-wired LoadImage nodes + image_* connections so we
        # can wire exactly as many references as we actually have.
        node3_inputs = base[prompt_node].setdefault("inputs", {})
        for k in list(node3_inputs.keys()):
            if k.startswith("model.images.image_"):
                del node3_inputs[k]
        for legacy in (self.nb_nodes.get("image_1"), self.nb_nodes.get("image_2")):
            base.pop(str(legacy), None)

        # Upload references once and add a LoadImage node per reference.
        # Skip unreadable/corrupt files (e.g. stale dry-run placeholders) so one
        # bad reference can't crash the whole generation.
        refs = []
        for r in (ref_paths or []):
            if _usable_ref(r):
                refs.append(r)
            elif r and os.path.isfile(r):
                print(f"    [skip ref] unreadable image ignored: {os.path.basename(r)}")
        refs = refs[:self.MAX_REFS]
        next_id = 100
        for i, ref in enumerate(refs, 1):
            up = self.api.upload_image(self.url, ref)
            nid = str(next_id); next_id += 1
            base[nid] = {"class_type": "LoadImage",
                         "inputs": {"image": up, "upload": "image"}}
            node3_inputs[f"model.images.image_{i}"] = [nid, 0]

        last_err = None
        for attempt in range(1, attempts + 1):
            g = _copy.deepcopy(base)
            n3 = g[prompt_node]["inputs"]
            n3["prompt"] = prompt
            n3["seed"] = _rng.randint(0, 2 ** 31)
            if self.image_model:
                n3["model"] = self.image_model
            if attempt == attempts:
                n3["response_modalities"] = "IMAGE+TEXT"
            try:
                result = self.api.run_graph(self.url, g, out_path, extra_data=self._extra())
                METER.add_image()
                return result
            except RuntimeError as e:
                if "did not generate an image" in str(e) and attempt < attempts:
                    last_err = e
                    print(f"    [retry {attempt}/{attempts}] Gemini returned no image; "
                          f"retrying with a new seed\u2026")
                    continue
                raise
        raise last_err

    def generate_pro(self, prompt, ref_paths, out_path, resolution="2K",
                     aspect="16:9", attempts=3):
        """Ultra-photorealistic generation via GeminiImage2Node ("Nano Banana Pro",
        model gemini-3-pro-image-preview).

        Unlike GeminiNanoBanana2V2 (separate image_1..N slots), this node takes a
        single batched IMAGE input, so references are chained through ImageBatch
        nodes into one batch. Used for the final locked hero image per scene.
        """
        import copy as _copy
        import random as _rng

        refs = [r for r in (ref_paths or []) if _usable_ref(r)][:self.MAX_REFS]

        base = {}
        load_ids = []
        nid = 100
        for r in refs:
            up = self.api.upload_image(self.url, r)
            base[str(nid)] = {"class_type": "LoadImage",
                              "inputs": {"image": up, "upload": "image"}}
            load_ids.append(str(nid)); nid += 1

        # Chain ImageBatch nodes to merge all references into one IMAGE batch.
        images_src = None
        if len(load_ids) == 1:
            images_src = [load_ids[0], 0]
        elif len(load_ids) >= 2:
            bid = 200
            cur = [load_ids[0], 0]
            for lid in load_ids[1:]:
                base[str(bid)] = {"class_type": "ImageBatch",
                                  "inputs": {"image1": cur, "image2": [lid, 0]}}
                cur = [str(bid), 0]; bid += 1
            images_src = cur

        gnode = {
            "class_type": "GeminiImage2Node",
            "inputs": {
                "prompt": prompt,
                "model": self.hero_model,
                "seed": 42,
                "aspect_ratio": aspect,
                "resolution": resolution,
                "response_modalities": "IMAGE",
                "system_prompt": (
                    "You are a world-class architectural photographer and a "
                    "hyper-photorealistic image generator. The output must be "
                    "indistinguishable from a real high-end DSLR photograph: "
                    "physically correct lighting, shadows and reflections, real "
                    "materials, accurate human anatomy and scale, realistic skin, "
                    "fabric, foliage, vehicles and roads, and natural depth of field. "
                    "Absolutely no CGI, 3D-render, illustration or videogame look."),
            },
        }
        if images_src:
            gnode["inputs"]["images"] = images_src
        base["3"] = gnode
        base["4"] = {"class_type": "SaveImage",
                     "inputs": {"images": ["3", 0], "filename_prefix": "archviz_pro"}}

        last_err = None
        for attempt in range(1, attempts + 1):
            g = _copy.deepcopy(base)
            g["3"]["inputs"]["seed"] = _rng.randint(0, 2 ** 31)
            if attempt == attempts:
                g["3"]["inputs"]["response_modalities"] = "IMAGE+TEXT"
            try:
                result = self.api.run_graph(self.url, g, out_path, extra_data=self._extra())
                METER.add_image(pro=True)
                return result
            except RuntimeError as e:
                if "no image" in str(e).lower() and attempt < attempts:
                    last_err = e
                    print(f"    [retry {attempt}/{attempts}] Pro returned no image; "
                          f"retrying with a new seed\u2026")
                    continue
                raise
        raise last_err

    def generate_audio(self, text, ref_audio_path, out_path, voice=None):
        """Generate voiceover via F5-TTS (with ref) or Kokoro TTS (fallback).

        F5-TTS recipe layout (node IDs from f5_tts.json):
          "1" LoadAudio        -> patch inputs.audio with uploaded voice_ref filename
          "2" F5TTSAudioInputs -> patch inputs.speech (gen text) + inputs.sample_text (ref transcript)
          "3" SaveAudio        -> patch inputs.filename_prefix
        Kokoro recipe layout (node IDs from kokoro_tts.json):
          "1" KokoroSpeaker    -> patch inputs.speaker_name
          "2" KokoroGenerator  -> patch inputs.text
          "3" SaveAudio        -> patch inputs.filename_prefix
        """
        prefix = os.path.splitext(os.path.basename(out_path))[0]
        if ref_audio_path and os.path.isfile(ref_audio_path):
            # F5-TTS: upload ref audio, optionally load companion .txt transcript.
            up = self.api.upload_audio(self.url, ref_audio_path)
            ref_txt_path = os.path.splitext(ref_audio_path)[0] + ".txt"
            ref_text = ID.read_text(ref_txt_path) if os.path.isfile(ref_txt_path) else ""
            patches = {
                self.f5_nodes["ref_audio"]: {"audio": up},
                self.f5_nodes["text"]:      {"speech": text, "sample_text": ref_text},
                self.f5_nodes["save"]:      {"filename_prefix": prefix},
            }
            result = self.api.run_recipe_audio(self.url, self.f5_recipe, patches, out_path,
                                              extra_data=self._extra())
            METER.add_tts()
            return result
        else:
            # Kokoro: just text + voice preset.
            patches = {
                self.kokoro_nodes["voice_node"]: {"speaker_name": voice or self.kokoro_voice},
                self.kokoro_nodes["text_node"]:  {"text": text},
                self.kokoro_nodes["save"]:       {"filename_prefix": prefix},
            }
            result = self.api.run_recipe_audio(self.url, self.kokoro_recipe, patches, out_path,
                                               extra_data=self._extra())
            METER.add_tts()
            return result

    def generate_director_video(self, frames_folder, segment_index, out_dir):
        """Stage M (director mode): queue ClaudeVideoGen_AIDirector workflow for one segment.

        Patch map (matches ltx_director_scene.json / ClaudeVideoGen_AIDirector.json):
          Node "438" AIDirectorProjectSetup  -> inputs.project_name
          Node "439" UnrealFrameIntake       -> inputs.selected_frames_folder
          Node "444" SegmentPromptPicker     -> inputs.segment_index, save_prefix_base
          Node "274" / "275" RandomNoise     -> inputs.noise_seed  (randomised each run)
        The final output video is emitted by SaveVideo node "372".
        """
        import random as _rng
        out_path = os.path.join(out_dir, "scene_video.mp4")
        patches = {
            "439": {"selected_frames_folder": str(frames_folder)},
            "444": {
                "segment_index":   segment_index,
                "save_prefix_base": f"scene_{segment_index:02d}",
            },
            "274": {"noise_seed": _rng.randint(0, 2 ** 31)},
            "275": {"noise_seed": _rng.randint(0, 2 ** 31)},
        }
        return self.api.run_recipe_video(
            self.url, self.director_recipe, patches, out_path,
            timeout=3600, video_node="372", extra_data=self._extra())

    def generate_video(self, positive, negative, hero_path, voiceover_path,
                       duration_secs, out_path, prefix="scene"):
        """Stage M: upload hero image + voiceover, queue LipSync LTX-2, download .mp4.

        Patch map (matches lipsync_scene.json):
          "304" CLIPTextEncode  -> inputs.text  (positive)
          "315" CLIPTextEncode  -> inputs.text  (negative)
          "436" AIDirectorLoadFrame -> inputs.image_path (absolute path)
          "414" LoadAudio       -> inputs.audio (uploaded voiceover filename)
          "418" TrimAudioDuration -> inputs.duration (float seconds)
          "273" SaveVideo       -> inputs.filename_prefix
        """
        up_audio = self.api.upload_audio(self.url, voiceover_path)
        n = self.lipsync_nodes
        patches = {
            n["positive"]: {"text":         positive},
            n["negative"]: {"text":         negative},
            n["image"]:    {"image_path":   os.path.abspath(hero_path)},
            n["audio"]:    {"audio":        up_audio},
            n["trim"]:     {"duration":     float(duration_secs)},
            n["save"]:     {"filename_prefix": prefix},
        }
        return self.api.run_recipe_video(
            self.url, self.lipsync_recipe, patches, out_path, timeout=3600,
            extra_data=self._extra())


# --------------------------------------------------------------------------- #
#  Parsing
# --------------------------------------------------------------------------- #
def parse_items(text):
    """Parse 'id | name | desc | location' lines into dicts."""
    items = []
    for line in (text or "").splitlines():
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        items.append({
            "id": parts[0], "name": parts[1] if len(parts) > 1 else parts[0],
            "desc": parts[2] if len(parts) > 2 else "",
            "location": parts[3] if len(parts) > 3 else "",
        })
    return items


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _format_cast(cast_info):
    lines = []
    sp = cast_info.get("spokesman", {})
    if sp:
        lines.append("Spokesman: " + ID.read_text(sp.get("desc_path", ""), "no desc"))
    for aid, a in cast_info.get("actors", {}).items():
        lines.append(f"Actor {aid}: " + ID.read_text(a.get("desc_path", ""), "no desc"))
    return "\n".join(lines) or "none"


def _format_inventory(root, n, furniture, objects):
    """Human-readable inventory string used in reconciliation prompts."""
    lines = []
    for it in furniture:
        lines.append(f"[furniture] {it['id']} '{it['name']}': {it['desc']} — {it['location']}")
    for it in objects:
        lines.append(f"[object] {it['id']} '{it['name']}': {it['desc']} — {it['location']}")
    cast = ID.load_index(root).get("cast", {})
    sp = cast.get("spokesman", {})
    if sp:
        lines.append("[person] spokesman: " + ID.read_text(sp.get("desc_path", ""), "no desc"))
    for aid, a in cast.get("actors", {}).items():
        lines.append(f"[person] {aid}: " + ID.read_text(a.get("desc_path", ""), "no desc"))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  Stages — Phase 1: A–F
# --------------------------------------------------------------------------- #
def stage_ingest(root, n, src_png):
    d = ID.init_scene(root, n)
    dst = os.path.join(d, "source.png")
    if os.path.abspath(src_png) != os.path.abspath(dst):
        import shutil
        shutil.copyfile(src_png, dst)
    return dst


def stage_analyze(root, n, src, client):
    d = ID.scene_dir(root, n)
    print("  analyzing scene: built elements...", flush=True)
    furn = client.analyze(P.ANALYSIS_FURNITURE, src)
    print("  analyzing scene: site objects...", flush=True)
    objs = client.analyze(P.ANALYSIS_OBJECTS, src)
    print("  analyzing scene: space / massing...", flush=True)
    space = client.analyze(P.ANALYSIS_SPACE, src)
    ID.write_text(os.path.join(d, "identity", "furniture", "_list.txt"), furn)
    ID.write_text(os.path.join(d, "identity", "objects", "_list.txt"), objs)
    ID.write_json(os.path.join(d, "identity", "space.json"), {"description": space})
    return parse_items(furn), parse_items(objs), space


def stage_packshots(root, n, furniture, objects, client):
    # The source screenshot anchors object identity (the model must extract the
    # real item from the real room, not invent a generic one).
    #
    # Cost control: a packshot is one NanoBanana credit each, so skip trivial site
    # clutter (parking lots, distant plant, planters, road markings...) and cap the
    # total. Primary built elements / furniture come first so they're never cut.
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    budget = LIMITS["max_packshots"]
    made = skipped = 0
    for kind, items, dirfn in (("furniture", furniture, ID.furniture_item_dir),
                               ("objects", objects, ID.object_item_dir)):
        for it in items:
            if LIMITS["skip_clutter"] and _is_clutter(it):
                skipped += 1
                continue
            if made >= budget:
                skipped += 1
                continue
            idir = dirfn(root, n, it["id"])
            ID.write_text(os.path.join(idir, "desc.txt"),
                          f"{it['name']} | {it['desc']} | {it['location']}")
            packshot = os.path.join(idir, "packshot_4view.png")
            _ensure(root, packshot, "packshot", lambda it=it, packshot=packshot: (
                print(f"    packshot {made + 1}/{budget}: {it['name']}...", flush=True),
                client.generate(P.fill(P.GEN_PACKSHOT_4VIEW,
                                       desc=f"{it['name']}, {it['desc']} ({it['location']})"),
                                [src], packshot)))
            ID.register_scene_item(root, n, kind, it["id"], {
                "name": it["name"], "desc_path": os.path.join(idir, "desc.txt"),
                "packshot": packshot, "location": it["location"]})
            made += 1
    if skipped:
        print(f"    [cost] {made} packshots generated, {skipped} skipped "
              f"(clutter/over budget of {budget})")


def stage_space_map(root, n, space, client):
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    out = os.path.join(ID.scene_dir(root, n), "identity", "space_map.png")
    _ensure(root, out, "space_map",
            lambda: client.generate(P.fill(P.GEN_SPACE_MAP, desc=space), [src], out))
    return out


def stage_stabilized_space(root, n, space, client):
    """Stage D: generate the clean empty-space hero conditioned on the real
    screenshot (ground truth) + all packshots."""
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    refs = [src] + ID.reference_images(root, n)  # screenshot first, then packshots
    out = os.path.join(ID.scene_dir(root, n), "renders", "empty_space_stabilized.png")
    _ensure(root, out, "stabilized_space",
            lambda: client.generate(P.fill(P.GEN_EMPTY_SPACE_STABILIZED, space=space),
                                    refs, out))
    ID.register_scene_space(root, n, {
        "desc_path": os.path.join(ID.scene_dir(root, n), "identity", "space.json"),
        "space_map": os.path.join(ID.scene_dir(root, n), "identity", "space_map.png"),
        "stabilized": out,
    })
    return out


def stage_cast(root, cast_spec, client):
    ID.init_project(root)
    sp = cast_spec.get("spokesman")
    if sp:
        sdir = os.path.join(ID.cast_dir(root), "spokesman")
        ID.write_text(os.path.join(sdir, "desc.txt"), sp["desc"])
        sheet = os.path.join(sdir, "sheet_7angle.png")
        portrait = os.path.join(sdir, "portrait.png")
        _ensure(root, sheet, "cast_sheet", lambda: (
            print("  cast: spokesman character sheet...", flush=True),
            client.generate(P.fill(P.GEN_CHARACTER_SHEET, n=7, desc=sp["desc"]), [], sheet)))
        _ensure(root, portrait, "cast_portrait", lambda: (
            print("  cast: spokesman portrait...", flush=True),
            client.generate(P.fill(P.GEN_PORTRAIT, desc=sp["desc"]), [], portrait)))
        for i, garment in enumerate(sp.get("clothes", []), 1):
            cp = os.path.join(sdir, "clothes_packshots", f"garment_{i:02d}.png")
            _ensure(root, cp, "cast_garment", lambda cp=cp, garment=garment: (
                print(f"  cast: spokesman garment {i}...", flush=True),
                client.generate(P.fill(P.GEN_CLOTHES_PACKSHOT, desc=garment), [], cp)))
        ID.register_cast(root, "spokesman", "spokesman",
                         {"desc_path": os.path.join(sdir, "desc.txt"),
                          "sheet": sheet, "portrait": portrait})
    for actor in cast_spec.get("actors", []):
        aid = actor["id"]
        adir = os.path.join(ID.cast_dir(root), "actors", aid)
        ID.write_text(os.path.join(adir, "desc.txt"), actor["desc"])
        sheet = os.path.join(adir, "sheet.png")
        portrait = os.path.join(adir, "portrait.png")
        _ensure(root, sheet, "cast_sheet", lambda aid=aid, actor=actor, sheet=sheet: (
            print(f"  cast: actor {aid} sheet...", flush=True),
            client.generate(P.fill(P.GEN_CHARACTER_SHEET, n=5, desc=actor["desc"]), [], sheet)))
        _ensure(root, portrait, "cast_portrait",
                lambda aid=aid, actor=actor, portrait=portrait: (
            print(f"  cast: actor {aid} portrait...", flush=True),
            client.generate(P.fill(P.GEN_PORTRAIT, desc=actor["desc"]), [], portrait)))
        ID.register_cast(root, "actors", aid,
                         {"desc_path": os.path.join(adir, "desc.txt"),
                          "sheet": sheet, "portrait": portrait})


# --------------------------------------------------------------------------- #
#  Stages — Phase 3: H (Audio)
# --------------------------------------------------------------------------- #
def stage_audio(root, n, client, kokoro_voice="af_heart"):
    """Stage H: generate voiceover WAV for lip-sync.

    Uses F5-TTS (voice cloning) if cast/spokesman/voice_ref.* exists,
    otherwise falls back to Kokoro TTS with the given voice preset.
    """
    audio_prompt = ID.read_text(
        os.path.join(ID.scene_dir(root, n), "prompts", "audio_prompt.txt"), "")
    if not audio_prompt.strip():
        # Fallback: use a trimmed master prompt if audio_prompt wasn't written yet.
        audio_prompt = ID.read_text(
            os.path.join(ID.scene_dir(root, n), "prompts", "master_prompt.txt"), "")[:300]
    if not audio_prompt.strip():
        # Final safety net — TTS nodes (Kokoro/F5) output None for empty text, which
        # crashes SaveAudio ("input audio is None"). Always send real words.
        audio_prompt = ("This is a place designed with intention, where light and "
                        "form come together to create something quietly remarkable.")

    ref_audio = ID.voice_ref_path(root)   # None if no voice_ref clip is present
    out_path   = os.path.join(ID.scene_dir(root, n), "audio", "voiceover.wav")
    engine = "F5-TTS (voice clone)" if ref_audio else f"Kokoro TTS ({kokoro_voice})"
    try:
        client.generate_audio(audio_prompt, ref_audio, out_path, voice=kokoro_voice)
    except Exception as e:
        # Voiceover is non-essential — never let a TTS failure abort the whole film.
        # The video stage falls back to a default clip duration when audio is absent.
        print(f"  [warn] voiceover skipped ({engine}): {e}")
        return None
    ID.register_audio(root, n, out_path)
    print(f"  voiceover done [{engine}] -> audio/voiceover.wav (Stage H)")
    return out_path


# --------------------------------------------------------------------------- #
#  Stages — Phase 4: M (Video)
# --------------------------------------------------------------------------- #
def _audio_duration_secs(path, fallback=8.0, min_secs=1.0, max_secs=120.0):
    """Return audio duration in seconds, clamped to a safe range.

    Handles WAV and any other format Kokoro/F5 may output (FLAC, MP3, OGG…).
    Tries soundfile first (handles all modern formats), then Python's wave module
    (pure WAV), then a FLAC STREAMINFO raw parser, then falls back.
    TrimAudioDuration crashes when duration ≤ 0 or > file length, so we clamp.
    """
    clamp = lambda d: max(min_secs, min(float(d), max_secs))  # noqa: E731

    # 1. soundfile — handles WAV, FLAC, OGG, MP3, AIFF, etc.
    try:
        import soundfile as _sf
        info = _sf.info(path)
        if info.duration > 0:
            return clamp(info.duration)
    except Exception:
        pass

    # 2. wave module — pure WAV only, but robust against LIST/INFO chunks.
    import wave as _wave
    try:
        with _wave.open(path, "rb") as wf:
            frames = wf.getnframes()
            rate   = wf.getframerate()
            if rate > 0 and frames > 0:
                return clamp(frames / float(rate))
    except Exception:
        pass

    # 3. Manual FLAC STREAMINFO parser (magic b"fLaC", block type 0).
    try:
        with open(path, "rb") as fh:
            raw = fh.read(46)
        if raw[:4] == b"fLaC":
            # METADATA_BLOCK_HEADER is 4 bytes (offset 4); STREAMINFO starts at 8.
            b = raw[18:26]   # bytes covering sample_rate + total_samples fields
            sample_rate    = (b[0] << 12) | (b[1] << 4) | (b[2] >> 4)
            total_samples  = (((b[3] & 0x0F) << 32)
                              | (b[4] << 24) | (b[5] << 16) | (b[6] << 8) | b[7])
            if sample_rate > 0 and total_samples > 0:
                return clamp(total_samples / sample_rate)
    except Exception:
        pass

    return fallback


def stage_video(root, n, client, director_frames=""):
    """Stage M: generate the scene video.

    Director mode (when director_frames is set):
        Queues the full ClaudeVideoGen_AIDirector workflow for this segment.
        ComfyUI's AI Director nodes handle frame analysis, prompts and audio
        internally — the GUI workflow in the browser controls all quality knobs.

    LipSync mode (default):
        Uploads the locked hero image + Kokoro/F5 voiceover and runs the
        LTX-2 lip-sync recipe.
    """
    d = ID.scene_dir(root, n)

    if director_frames and hasattr(client, "generate_director_video"):
        renders = os.path.join(d, "renders")
        os.makedirs(renders, exist_ok=True)
        out_path = os.path.join(renders, "scene_video.mp4")
        client.generate_director_video(director_frames, n, renders)
        ID.register_video(root, n, out_path)
        print(f"  video done [LTX Director] -> renders/scene_video.mp4 (Stage M)")
        return out_path

    # --- LipSync (original) path ---
    hero      = os.path.join(d, "renders", "hero_locked.png")
    voiceover = os.path.join(d, "audio", "voiceover.wav")
    positive  = ID.read_text(os.path.join(d, "prompts", "positive.txt"), "")
    negative  = ID.read_text(os.path.join(d, "prompts", "negative.txt"), "")

    if not os.path.isfile(hero):
        hero = os.path.join(d, "source.png")
    if not positive:
        positive = ID.read_text(os.path.join(d, "prompts", "master_prompt.txt"), "")
    if not negative:
        negative = ("blurry, low quality, still frame, watermark, cartoon, "
                    "duplicate person, frozen image, oversaturated, jump cut")

    duration = _audio_duration_secs(voiceover) if os.path.isfile(voiceover) else 8.0
    out_path  = os.path.join(d, "renders", "scene_video.mp4")
    prefix    = f"{ID.scene_id(n)}/video/scene"

    client.generate_video(positive, negative, hero, voiceover, duration, out_path, prefix)
    ID.register_video(root, n, out_path)
    print(f"  video done [{duration:.1f}s] -> renders/scene_video.mp4 (Stage M)")
    return out_path


# --------------------------------------------------------------------------- #
#  Stages — Phase 2: G, I, J, K, L
# --------------------------------------------------------------------------- #
def stage_coordinates(root, n, src, client):
    """Stage G (part 1): vision model extracts x/y/area coordinates for every element."""
    raw = client.analyze(P.ANALYSIS_COORDINATES, src)
    try:
        coords = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        try:
            coords = json.loads(m.group(0)) if m else []
        except (json.JSONDecodeError, TypeError):
            coords = []
    coords = _normalize_coords(coords, src)
    path = os.path.join(ID.scene_dir(root, n), "coordinates.json")
    ID.write_json(path, coords)
    return coords


def _normalize_coords(coords, src):
    """Coerce x/y to fractions of 0..1 for the hero composite.

    Vision models disagree on coordinate conventions even when asked for 0..1:
      * already-normalized -> values <= 1.5, left as-is
      * Gemini 0..1000 grid -> values up to ~1000, divided by 1000
      * raw pixels         -> larger than the 0..1000 grid, divided by image size
    """
    if not isinstance(coords, list) or not coords:
        return coords if isinstance(coords, list) else []
    try:
        xs = [c["x"] for c in coords if isinstance(c, dict) and isinstance(c.get("x"), (int, float))]
        ys = [c["y"] for c in coords if isinstance(c, dict) and isinstance(c.get("y"), (int, float))]
        if not xs or not ys:
            return coords
        mx, my = max(xs), max(ys)
        if mx <= 1.5 and my <= 1.5:
            return coords   # already normalized
        if mx <= 1000 and my <= 1000:
            wx = wy = 1000.0   # Gemini's 0..1000 normalized grid
        else:
            wx, wy = mx, my    # assume pixels; fall back to image dims
            try:
                from PIL import Image
                with Image.open(src) as im:
                    wx, wy = im.size
            except Exception:
                pass
        for c in coords:
            if isinstance(c, dict):
                if isinstance(c.get("x"), (int, float)):
                    c["x"] = round(min(max(c["x"] / wx, 0.0), 1.0), 4)
                if isinstance(c.get("y"), (int, float)):
                    c["y"] = round(min(max(c["y"] / wy, 0.0), 1.0), 4)
    except Exception:
        pass
    return coords


def stage_master_prompt(root, n, furniture, objects, space, client, prev_master=""):
    """Stage G (part 2): master prompt for the next scene + audio prompt.

    If `prev_master` is provided (scene N-1's master_prompt), it is injected as
    context so the model knows where we are arriving FROM, enabling cross-scene
    continuity.
    """
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    cast_text = _format_cast(ID.load_index(root).get("cast", {}))
    furn_text = "\n".join(f"{it['id']} | {it['name']} | {it['desc']} | {it['location']}"
                          for it in furniture)
    obj_text  = "\n".join(f"{it['id']} | {it['name']} | {it['desc']} | {it['location']}"
                          for it in objects)
    prev_ctx = (f"\n\nPREVIOUS SCENE CONTEXT (the scene we are arriving from):\n"
                f"{prev_master[:600]}") if prev_master.strip() else ""
    master = client.analyze(
        P.fill(P.MASTER_PROMPT, space=space, furniture=furn_text,
               objects=obj_text, cast=cast_text) + prev_ctx, src)
    if not (master or "").strip():
        # Never leave the master prompt empty — fall back to the space description
        # (or a generic line) so downstream audio/video stages always have text.
        master = (space or "").strip() or (
            "A cinematic, photorealistic establishing shot of this architectural "
            "scene, presented with clarity, depth and confident composition.")
    audio = client.analyze(
        "Write a 2–3 sentence voiceover narration for a luxury ArchViz branded film "
        "based on this scene description. Elegant, third person, concise: " + master[:400],
        src)
    if not (audio or "").strip():
        # Guard against an empty narration so Kokoro/F5 never receive blank text.
        audio = ("This is a place designed with intention — where light, form and "
                 "space come together to create something quietly remarkable.")
    prompts_dir = os.path.join(ID.scene_dir(root, n), "prompts")
    ID.write_text(os.path.join(prompts_dir, "master_prompt.txt"), master)
    ID.write_text(os.path.join(prompts_dir, "audio_prompt.txt"), audio)
    return master, audio


def stage_photoreal_each_variation(root, n, variations, src, space, client,
                                   attempts=3, min_score=90, resolution="2K"):
    """Stage I.5: upgrade every Flash hero variation to Nano Banana Pro immediately
    after generation so the inspector (Stage K) always scores the photorealistic
    version.  Each variation is replaced in-place; a side-car *_pro.png is kept
    as the reuse-cache so re-runs skip the expensive Pro call when unchanged.

    min_score = 90  (practical interpretation of the user's "99% logical and
    photorealistic" target — the realism inspector's 0-100 scale tops out around
    95-98 for a genuine DSLR photo, so 90 is the right gate for near-perfect output)
    """
    for i, var_path in enumerate(variations, 1):
        if not os.path.isfile(var_path):
            continue
        pro_cache = var_path.replace(".png", "_pro.png")
        # Reuse if the Pro version already passed the gate.
        if CACHE.should_skip(pro_cache, kind="hero_pro",
                             min_score=LIMITS.get("photoreal_min", min_score)):
            print(f"    [reuse] var {i} Pro render (passed quality gate)", flush=True)
            shutil.copyfile(pro_cache, var_path)
            continue

        refs     = [src, var_path] + list(ID.reference_images(root, n))
        tmp      = var_path + "._pro_try.png"
        feedback = ""
        best_score = -1
        for attempt in range(1, max(1, attempts) + 1):
            print(f"    photoreal var {i}/{len(variations)} "
                  f"(Pro attempt {attempt}/{attempts})...", flush=True)
            prompt = P.fill(P.GEN_PHOTOREAL_VARIATION, space=space) + feedback
            try:
                client.generate_pro(prompt, refs, tmp, resolution=resolution)
            except Exception as exc:
                print(f"    [photoreal] Pro render error: {exc}", flush=True)
                break
            raw = client.analyze(P.REALISM_INSPECTOR, tmp)
            try:
                parsed   = json.loads(raw)
                score    = int(parsed.get("score", 0))
                problems = parsed.get("problems", [])
            except (json.JSONDecodeError, TypeError, ValueError):
                m = re.search(r'"score"\s*:\s*(\d+)', raw or "")
                score, problems = (int(m.group(1)) if m else 0), []
            print(f"      realism score: {score}%", flush=True)
            if score > best_score:
                best_score = score
                shutil.copyfile(tmp, pro_cache)
            if score >= min_score:
                break
            if problems:
                feedback = ("\n\nFIX THESE PHOTOREALISM PROBLEMS FROM PREVIOUS "
                            "ATTEMPT: " + "; ".join(str(p) for p in problems))

        # Replace Flash variation with best Pro render.
        if os.path.isfile(pro_cache):
            shutil.copyfile(pro_cache, var_path)
            CACHE.record(pro_cache, kind="hero_pro", score=best_score)
            print(f"    var {i} upgraded [realism {best_score}%]", flush=True)
        try:
            os.remove(tmp)
        except OSError:
            pass


def stage_hero_composite(root, n, space, coords, client, prev_master=""):
    """Stage I: generate hero-composite variations (space + cast + coords).

    Variation count is LIMITS["hero_variations"] (each is one NanoBanana credit).
    `prev_master` provides the incoming-scene context for cross-scene continuity.
    """
    # Screenshot first (space ground truth), then character sheets + packshots.
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    refs = [src] + list(ID.reference_images(root, n))
    coords_str = json.dumps(coords)
    prev_ctx = (f" Arriving from: {prev_master[:300]}.") if prev_master.strip() else ""
    prompt = P.fill(P.GEN_HERO_COMPOSITE, space=space, coords=coords_str) + prev_ctx
    variations = []
    count = max(1, int(LIMITS.get("hero_variations", 4)))
    for v in range(1, count + 1):
        out = os.path.join(ID.scene_dir(root, n), "renders", f"hero_v{v}.png")
        _ensure(root, out, "hero", lambda v=v, out=out: (
            print(f"    hero variation {v}/{count}...", flush=True),
            client.generate(prompt, list(refs), out)))   # fresh copy of refs each call
        variations.append(out)
    return variations


def stage_reconcile(root, n, furniture, objects, coords, client):
    """Stage J: reconcile positive prompt so every action binds to a real element."""
    from nodes.object_lock import AIDirectorObjectLock
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    master = ID.read_text(os.path.join(ID.scene_dir(root, n), "prompts", "master_prompt.txt"), "")
    inventory = _format_inventory(root, n, furniture, objects)
    rewritten = client.analyze(
        P.fill(P.RECONCILE, inventory=inventory,
               coords=json.dumps(coords), prompt=master), src)
    scene_objects = "; ".join(f"{it['name']} ({it['location']})"
                              for it in furniture + objects)
    positive, negative = AIDirectorObjectLock().amend(rewritten, "strict",
                                                      scene_objects=scene_objects)
    prompts_dir = os.path.join(ID.scene_dir(root, n), "prompts")
    ID.write_text(os.path.join(prompts_dir, "positive.txt"), positive)
    ID.write_text(os.path.join(prompts_dir, "negative.txt"), negative)
    return positive, negative


def _inventory_text(furniture, objects, cast, root):
    """Build a compact inventory string of every locked element for the inspector."""
    lines = []
    for it in furniture:
        lines.append(f"{it['id']} ({it['name']}): {it['desc']}")
    for it in objects:
        lines.append(f"{it['id']} ({it['name']}): {it['desc']}")
    sp = cast.get("spokesman", {})
    if sp:
        lines.append("spokesman: " + ID.read_text(sp.get("desc_path", ""), "spokesman"))
    for aid, a in cast.get("actors", {}).items():
        lines.append(f"{aid}: " + ID.read_text(a.get("desc_path", ""), aid))
    return "\n".join(lines)


def stage_inspect(root, n, furniture, objects, variations, client):
    """Stage K: score hero variations against the locked inventory; emit report.json.

    Cost control via LIMITS["inspect_mode"]:
      off  -> no Gemini calls; first variation chosen.
      fast -> ONE holistic Gemini call per variation (default).
      full -> one Gemini call PER element PER variation (most expensive).
    """
    idx = ID.load_index(root)
    cast = idx.get("cast", {})
    mode = LIMITS.get("inspect_mode", "fast")

    # OFF: skip scoring entirely, keep the first variation.
    if mode == "off" or not variations:
        var_results = [{
            "variation": f"hero_v{i}.png", "path": p, "scores": [],
            "average": 0.0, "min_score": 0, "passes_gate": False,
            "hallucinations": [], "skipped": True,
        } for i, p in enumerate(variations, 1)]
        report = {"scene": ID.scene_id(n), "gate_threshold": 90,
                  "inspect_mode": "off", "variations": var_results,
                  "best_variation": var_results[0]["variation"] if var_results else None,
                  "best_passes_gate": False}
        ID.write_json(os.path.join(ID.scene_dir(root, n), "report.json"), report)
        return report, 0

    elements = []
    for it in furniture:
        elements.append({"id": it["id"], "label": it["name"], "desc": it["desc"]})
    for it in objects:
        elements.append({"id": it["id"], "label": it["name"], "desc": it["desc"]})
    sp = cast.get("spokesman", {})
    if sp:
        elements.append({"id": "spokesman", "label": "spokesman",
                         "desc": ID.read_text(sp.get("desc_path", ""), "spokesman")})
    for aid, a in cast.get("actors", {}).items():
        elements.append({"id": aid, "label": aid,
                         "desc": ID.read_text(a.get("desc_path", ""), aid)})

    inventory = _inventory_text(furniture, objects, cast, root)
    var_results = []
    best_idx, best_avg = 0, -1.0
    for vi, hero_path in enumerate(variations, 1):
        print(f"    scoring hero {vi}/{len(variations)} (inspect={mode})...", flush=True)
        scores, hallu_all = [], []
        if mode == "fast":
            # One holistic call scores the whole composite (≈17x cheaper).
            raw = client.analyze(P.fill(P.INSPECTOR_HOLISTIC, inventory=inventory),
                                 hero_path)
            try:
                parsed = json.loads(raw)
                overall = int(parsed.get("score", 0))
                hallu_all.extend(parsed.get("hallucinations", []))
            except (json.JSONDecodeError, TypeError, ValueError):
                m = re.search(r'"score"\s*:\s*(\d+)', raw or "")
                overall = int(m.group(1)) if m else 0
            scores.append({"element": "overall", "label": "overall", "score": overall})
        else:  # full
            for el in elements:
                raw = client.analyze(
                    P.fill(P.INSPECTOR, label=el["label"], desc=el["desc"]), hero_path)
                try:
                    parsed = json.loads(raw)
                    score = int(parsed.get("score", 0))
                    hallu_all.extend(parsed.get("hallucinations", []))
                except (json.JSONDecodeError, TypeError, ValueError):
                    score = 0
                scores.append({"element": el["id"], "label": el["label"], "score": score})
        avg = sum(s["score"] for s in scores) / len(scores) if scores else 0.0
        min_score = min((s["score"] for s in scores), default=0)
        var_results.append({
            "variation": f"hero_v{vi}.png",
            "path": hero_path,
            "scores": scores,
            "average": round(avg, 1),
            "min_score": min_score,
            "passes_gate": min_score >= 90,
            "hallucinations": hallu_all,
        })
        if avg > best_avg:
            best_avg, best_idx = avg, vi - 1

    # Prefer first variation that fully passes; fall back to highest average.
    for i, vr in enumerate(var_results):
        if vr["passes_gate"]:
            best_idx = i
            break

    report = {
        "scene": ID.scene_id(n),
        "gate_threshold": 90,
        "inspect_mode": mode,
        "variations": var_results,
        "best_variation": var_results[best_idx]["variation"],
        "best_passes_gate": var_results[best_idx]["passes_gate"],
    }
    ID.write_json(os.path.join(ID.scene_dir(root, n), "report.json"), report)
    return report, best_idx


def stage_lock_hero(root, n, variations, best_idx, report):
    """Stage L: copy the chosen passing variation to hero_locked.png."""
    locked = os.path.join(ID.scene_dir(root, n), "renders", "hero_locked.png")
    shutil.copyfile(variations[best_idx], locked)
    vr = report["variations"][best_idx]
    status = "PASS" if vr["passes_gate"] else f"BEST_AVAILABLE avg={vr['average']:.0f}%"
    print(f"  hero_locked.png <- {vr['variation']} [{status}]")
    return locked


def stage_photoreal_finish(root, n, client, attempts=2, min_score=85, resolution="2K"):
    """Stage P: re-render the locked hero with Nano Banana Pro for maximum realism.

    Takes the locked composition (identity + layout already correct) and renders an
    ultra-photorealistic version with gemini-3-pro-image. A realism inspector scores
    the result and, if it falls short, regenerates with the inspector's feedback
    appended (up to `attempts` tries). The best result overwrites hero_locked.png.
    """
    scene_d = ID.scene_dir(root, n)
    locked = os.path.join(scene_d, "renders", "hero_locked.png")
    if not os.path.isfile(locked):
        print("  [photoreal] no locked hero yet - skipping Stage P")
        return None

    final = os.path.join(scene_d, "renders", "hero_photoreal.png")
    # Reuse a previously-approved photoreal render if it's still good.
    if CACHE.should_skip(final, kind="hero_pro"):
        print(f"    [reuse] {os.path.relpath(final, root)} (passed quality gate)", flush=True)
        shutil.copyfile(final, locked)
        return final

    src = os.path.join(scene_d, "source.png")
    space = ID.read_json(os.path.join(scene_d, "identity", "space.json"),
                         {}).get("description", "")
    # Order matters: source screenshot FIRST (spatial/proportional ground truth),
    # then the locked composition (people positions), then identity packshots.
    # The prompt tells the model: fix CGI artifacts from the locked image using the
    # real photograph as the authority on scale, proportions and materials.
    refs = [src, locked] + list(ID.reference_images(root, n))
    tmp = os.path.join(scene_d, "renders", "_photoreal_try.png")
    feedback = ""
    best_score = -1
    for attempt in range(1, max(1, attempts) + 1):
        print(f"    photoreal finish (Nano Banana Pro {resolution}) "
              f"attempt {attempt}/{attempts}...", flush=True)
        prompt = P.fill(P.GEN_PHOTOREAL_HERO, space=space) + feedback
        client.generate_pro(prompt, refs, tmp, resolution=resolution)
        raw = client.analyze(P.REALISM_INSPECTOR, tmp)
        try:
            parsed = json.loads(raw)
            score = int(parsed.get("score", 0))
            problems = parsed.get("problems", [])
        except (json.JSONDecodeError, TypeError, ValueError):
            m = re.search(r'"score"\s*:\s*(\d+)', raw or "")
            score = int(m.group(1)) if m else 0
            problems = []
        print(f"      realism score: {score}%", flush=True)
        if score > best_score:
            best_score = score
            shutil.copyfile(tmp, final)
        if score >= min_score:
            break
        if problems:
            feedback = ("\n\nFIX THESE PHOTOREALISM PROBLEMS FROM THE PREVIOUS "
                        "ATTEMPT: " + "; ".join(str(p) for p in problems))

    if os.path.isfile(final):
        shutil.copyfile(final, locked)   # the locked hero is now the photoreal one
        CACHE.record(final, kind="hero_pro", score=best_score)
        print(f"  hero_locked.png upgraded -> photoreal [realism {best_score}%] (Stage P)")
    try:
        os.remove(tmp)
    except OSError:
        pass
    return final


# --------------------------------------------------------------------------- #
#  Scene runner (phases 1–4)
# --------------------------------------------------------------------------- #
def run_scene(root, n, src, client, phases=(1, 2, 3, 4),
              _kokoro_voice="af_heart", prev_master="", director_frames=""):
    print(f"\n=== SCENE {n} ({client.label}) ===")

    if 1 in phases:
        src = stage_ingest(root, n, src)
        furniture, objects, space = stage_analyze(root, n, src, client)
        print(f"  furniture: {len(furniture)}  objects: {len(objects)}")
        stage_packshots(root, n, furniture, objects, client)
        stage_space_map(root, n, space, client)
        print("  packshots + space map done (Stages B–C)")
        stage_stabilized_space(root, n, space, client)
        print("  stabilized space done (Stage D)")
    else:
        # Phase 2 re-run: re-read what Phase 1 wrote.
        src = os.path.join(ID.scene_dir(root, n), "source.png")
        d = ID.scene_dir(root, n)
        furn_raw = ID.read_text(os.path.join(d, "identity", "furniture", "_list.txt"))
        obj_raw  = ID.read_text(os.path.join(d, "identity", "objects",   "_list.txt"))
        space    = ID.read_json(os.path.join(d, "identity", "space.json"),
                                {}).get("description", "")
        furniture, objects = parse_items(furn_raw), parse_items(obj_raw)

    # If a locked hero from a previous run already passed inspection, skip the
    # whole expensive composite+reconcile+inspect block (and its master/coords).
    scene_d = ID.scene_dir(root, n)
    hero_skip = (2 in phases) and CACHE.hero_reusable(scene_d)
    if hero_skip:
        print("  [reuse] hero_locked.png already passed inspection "
              "- skipping Stages G/I/J/K", flush=True)

    if 2 in phases and not hero_skip:
        coords = stage_coordinates(root, n, src, client)
        print(f"  coordinates: {len(coords)} elements (Stage G)")
        master, _ = stage_master_prompt(root, n, furniture, objects, space, client,
                                        prev_master=prev_master)
        print("  master prompt + audio prompt done (Stage G)"
              + (f" [with scene {n-1} context]" if prev_master else ""))
    else:
        master = ID.read_text(
            os.path.join(scene_d, "prompts", "master_prompt.txt"), "")

    if 3 in phases:
        stage_audio(root, n, client, kokoro_voice=_kokoro_voice)

    if 2 in phases and not hero_skip:
        variations = stage_hero_composite(root, n, space, coords, client,
                                          prev_master=prev_master)
        print(f"  hero composite: {len(variations)} variations (Stage I)")

        # Stage I.5: upgrade every variation to Nano Banana Pro BEFORE the inspector
        # so Stage K always scores photorealistic images.  A 90% realism gate ensures
        # only near-real renders advance; feedback from the inspector loop fixes problems.
        if LIMITS.get("photoreal", True):
            print("  upgrading all variations to Pro (Stage I.5)...", flush=True)
            stage_photoreal_each_variation(
                root, n, variations, src, space, client,
                attempts=LIMITS.get("photoreal_attempts", 3),
                min_score=LIMITS.get("photoreal_min", 90),
                resolution=LIMITS.get("hero_resolution", "2K"))
            print("  all variations upgraded to Pro (Stage I.5)")

        stage_reconcile(root, n, furniture, objects, coords, client)
        print("  prompts reconciled (Stage J)")
        report, best_idx = stage_inspect(root, n, furniture, objects, variations, client)
        vr = report["variations"][best_idx]
        gate = "PASS" if vr["passes_gate"] else f"best={vr['average']:.0f}%"
        print(f"  inspector done [{gate}] (Stage K)")
        stage_lock_hero(root, n, variations, best_idx, report)

    if 4 in phases:
        stage_video(root, n, client, director_frames=director_frames)

    print(METER.log_line(), flush=True)
    return {"furniture": furniture, "objects": objects, "space": space,
            "master": master}


# --------------------------------------------------------------------------- #
#  Phase 5 — compose
# --------------------------------------------------------------------------- #
def _find_ffmpeg():
    import shutil as _sh
    exe = _sh.which("ffmpeg")
    if exe:
        return exe
    # Common winget install location (PATH not yet refreshed in current shell).
    _winget_link = os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe")
    if os.path.isfile(_winget_link):
        return _winget_link
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def stage_compose(root, out_path=None, mode="hard_cut_reencode",
                  fps=24, crossfade_secs=0.5, clip_duration=8):
    """Phase 5 (Stage N): collect all scene_XX/renders/scene_video.mp4 in scene order
    and concatenate them into the final branded film using ffmpeg.

    mode options:
      hard_cut_reencode  — safe, re-encodes to h264 mp4 (default)
      hard_cut_copy      — stream-copy (fastest, needs compatible codecs)
      crossfade          — xfade dissolve between clips
    """
    import tempfile, glob as _glob

    scenes_dir = os.path.join(root, "scenes")
    clips = sorted(
        _glob.glob(os.path.join(scenes_dir, "scene_*", "renders", "scene_video.mp4")),
        key=lambda p: p  # sorted() on path already gives scene_01 < scene_02 …
    )
    if not clips:
        print("  [compose] no scene_video.mp4 files found — skipping.")
        return None

    print(f"  [compose] {len(clips)} clip(s):")
    for c in clips:
        print(f"    {c}")

    out_path = out_path or os.path.join(root, "final_film.mp4")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print("  [compose] ffmpeg not found — writing clip list to final_film.txt instead.")
        with open(out_path.replace(".mp4", ".txt"), "w", encoding="utf-8") as fh:
            for c in clips:
                fh.write(c + "\n")
        return out_path.replace(".mp4", ".txt")

    # Check for dry-run sentinel clips (4-byte placeholders) — skip actual concat.
    dry = all(os.path.getsize(c) <= 16 for c in clips)
    if dry:
        with open(out_path, "wb") as fh:
            fh.write(b"DRY_COMPOSE\n")
        print(f"  [compose] dry-run sentinel -> {os.path.basename(out_path)}")
        return out_path

    fd, listfile = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for c in clips:
                safe = c.replace("'", "'\\''")
                fh.write(f"file '{safe}'\n")

        if mode == "crossfade" and len(clips) > 1 and crossfade_secs > 0:
            cmd = [ffmpeg, "-y"]
            for c in clips:
                cmd += ["-i", c]
            filt, prev, offset = [], "[0:v]", clip_duration - crossfade_secs
            for i in range(1, len(clips)):
                out_tag = f"[v{i}]"
                filt.append(f"{prev}[{i}:v]xfade=transition=fade:"
                            f"duration={crossfade_secs}:offset={offset:.3f}{out_tag}")
                prev = out_tag
                offset += clip_duration - crossfade_secs
            cmd += ["-filter_complex", ";".join(filt), "-map", prev,
                    "-r", str(fps), "-c:v", "libx264", "-crf", "18",
                    "-preset", "medium", "-pix_fmt", "yuv420p", out_path]
        elif mode == "hard_cut_copy":
            cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
                   "-c", "copy", out_path]
        else:
            cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
                   "-r", str(fps), "-c:v", "libx264", "-crf", "18",
                   "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", out_path]

        import subprocess as _sp
        r = _sp.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print("  [compose] ffmpeg FAILED:")
            print(r.stderr[-1500:])
            return None
    finally:
        try:
            os.unlink(listfile)
        except OSError:
            pass

    size_mb = os.path.getsize(out_path) / (1024 * 1024) if os.path.isfile(out_path) else 0
    print(f"  [compose] DONE -> {out_path} ({size_mb:.1f} MB)")
    ID.write_json(os.path.join(root, "compose_manifest.json"), {
        "clips": clips, "output": out_path, "mode": mode, "fps": fps,
    })
    return out_path


DEFAULT_CAST = {
    "spokesman": {"desc": "a woman in her early 30s, golden-brown wavy hair, tailored "
                          "cream trench coat, warm confident presence",
                  "clothes": ["cream tailored trench coat"]},
    "actors": [{"id": "a01", "desc": "a man in his 40s, short dark hair, navy suit"}],
}


def main():
    ap = argparse.ArgumentParser(description="ArchViz Scene Director")
    ap.add_argument("--project", required=True, help="project root folder")
    ap.add_argument("--frames", required=True, help="folder of scene screenshots")
    ap.add_argument("--scene", type=int, default=0, help="single scene number (0 = all)")
    ap.add_argument("--scene-type", default="interior",
                    choices=["interior", "exterior"],
                    help="interior = furniture/props analysis (default); exterior = "
                         "buildings, façades, landscaping, vehicles, signage. Switches "
                         "the Qwen analysis + NanoBanana packshot/master prompts so "
                         "element-identity locking works on exterior ArchViz scenes.")
    ap.add_argument("--cast", default="", help="cast spec JSON (spokesman + actors)")
    ap.add_argument("--comfy-url", default="http://127.0.0.1:8000")
    ap.add_argument("--recipes", default="", help="dir of API-format recipe graphs")
    ap.add_argument("--dry-run", action="store_true", help="offline scaffold with placeholders")
    ap.add_argument("--phases", default="1,2,3,4,5",
                    help="comma-separated phases to run (default: 1,2,3,4,5). "
                         "Phase 5 = final film compose only; can be run standalone.")
    ap.add_argument("--kokoro-voice", default="af_heart",
                    help="Kokoro TTS voice preset used when no voice_ref clip is present "
                         "(default: af_heart). See Kokoro docs for available voices.")
    ap.add_argument("--compose-mode", default="hard_cut_reencode",
                    choices=["hard_cut_reencode", "hard_cut_copy", "crossfade"],
                    help="ffmpeg assembly mode for Phase 5 (default: hard_cut_reencode)")
    ap.add_argument("--crossfade-secs", type=float, default=0.5,
                    help="crossfade duration in seconds when --compose-mode=crossfade "
                         "(default: 0.5)")
    ap.add_argument("--clip-duration", type=int, default=8,
                    help="expected clip duration in seconds, used for crossfade timing "
                         "(default: 8)")
    ap.add_argument("--output-film", default="",
                    help="output path for the final composed film "
                         "(default: <project>/final_film.mp4)")
    # --- Cost controls (each NanoBanana/Gemini call costs API credits) ---
    ap.add_argument("--max-packshots", type=int, default=8,
                    help="max element packshots to generate per scene (default 8). "
                         "Lower = fewer NanoBanana credits.")
    ap.add_argument("--inspect-mode", default="fast", choices=["off", "fast", "full"],
                    help="QA scoring cost: off = none (pick first hero), fast = 1 "
                         "Gemini call per hero variation (default), full = 1 per "
                         "element per variation (most expensive).")
    ap.add_argument("--hero-variations", type=int, default=4,
                    help="how many hero composites to generate per scene "
                         "(default 4; each is one NanoBanana credit).")
    ap.add_argument("--keep-clutter", action="store_true",
                    help="also packshot trivial site clutter (parking, planters, "
                         "distant plant...). Off by default to save credits.")
    # --- Ultra-photoreal locked image (Nano Banana Pro) ---
    ap.add_argument("--no-photoreal", action="store_true",
                    help="skip the Nano Banana Pro photoreal finish on the locked "
                         "hero image (Stage P). On by default.")
    ap.add_argument("--hero-resolution", default="2K", choices=["1K", "2K", "4K"],
                    help="output resolution for the Pro photoreal locked image "
                         "(default 2K; 4K costs more credits).")
    ap.add_argument("--hero-model", default="gemini-3-pro-image-preview",
                    help="image model for the locked hero (default Nano Banana Pro "
                         "= gemini-3-pro-image-preview).")
    ap.add_argument("--photoreal-min", type=int, default=85,
                    help="realism score 0-100 the locked image must reach before the "
                         "photoreal loop stops (default 85).")
    ap.add_argument("--photoreal-attempts", type=int, default=2,
                    help="max Nano Banana Pro re-renders per scene to hit the realism "
                         "target (default 2).")
    ap.add_argument("--no-reuse", action="store_true",
                    help="regenerate everything; do NOT reuse existing good assets. "
                         "By default, existing elements that pass the quality gate "
                         "are reused to save credits.")
    ap.add_argument("--reuse-min-score", type=int, default=70,
                    help="min stored fit score (0-100) for an existing asset to be "
                         "reused (default 70). Applies to scored assets/heroes.")
    ap.add_argument("--rescore-existing", action="store_true",
                    help="pay for a fresh vision fit-score when reusing assets that "
                         "have no cached score (off by default).")
    ap.add_argument("--vision-backend", default="gemini", choices=["gemini", "qwen"],
                    help="vision analysis backend for Stages B/G/J/K. 'gemini' (default) "
                         "uses the Gemini API node (reliable, high quality). 'qwen' uses "
                         "the local Qwen3-VL GGUF (needs a recent llama-cpp-python with "
                         "Qwen3VLChatHandler).")
    ap.add_argument("--vision-model", default="gemini-3-1-pro",
                    help="Gemini model id for vision analysis (default: gemini-3-1-pro).")
    ap.add_argument("--qwen-model-path",
                    default=r"C:\Users\oranbenshaprut\Documents\ComfyUI\models\LLM\Qwen3VL-8B-Instruct-Q8_0.gguf",
                    help="path to Qwen3-VL GGUF model file")
    ap.add_argument("--qwen-mmproj-path",
                    default=r"C:\Users\oranbenshaprut\Documents\ComfyUI\models\LLM\mmproj-Qwen3VL-8B-Instruct-F16.gguf",
                    help="path to Qwen3-VL mmproj GGUF file")
    ap.add_argument("--api-token", default="",
                    help="ComfyOrg API key for paid API nodes (Gemini/NanoBanana). "
                         "Sent as extra_data.api_key_comfy_org. Leave empty if ComfyUI "
                         "is already logged in to a ComfyOrg account.")
    ap.add_argument("--image-model", default="Nano Banana 2 (Gemini 3.1 Flash Image)",
                    help="model selected on the GeminiNanoBanana2V2 node.")
    # --- Director mode (uses ClaudeVideoGen_AIDirector workflow) ---
    ap.add_argument("--director-frames", default="",
                    help="path to Unreal Engine frames folder — activates director mode "
                         "for Phase 4. The full ClaudeVideoGen_AIDirector workflow runs in "
                         "ComfyUI; all quality knobs are set in the GUI workflow.")
    ap.add_argument("--director-recipe", default="",
                    help="path to ltx_director_scene.json "
                         "(default: <recipes-dir>/ltx_director_scene.json)")
    args = ap.parse_args()

    phases = set(int(p.strip()) for p in args.phases.split(",") if p.strip())

    # Exterior mode: repoint the analysis/packshot/master prompts to the EXTERIOR
    # variants. The stages read these P.* attributes at call time, so reassigning
    # them here (before the scene loop) swaps the whole pipeline's vocabulary from
    # interior furnishings to buildings, façades, landscaping, vehicles and signage.
    P.apply_scene_type(args.scene_type)
    if args.scene_type == "exterior":
        print("[scene-type] EXTERIOR ArchViz mode "
              "(buildings, façades, landscaping, vehicles, signage)")

    # Apply cost controls.
    LIMITS["max_packshots"]   = max(0, args.max_packshots)
    LIMITS["inspect_mode"]    = args.inspect_mode
    LIMITS["hero_variations"] = max(1, args.hero_variations)
    LIMITS["skip_clutter"]    = not args.keep_clutter
    LIMITS["photoreal"]          = not args.no_photoreal
    LIMITS["hero_resolution"]    = args.hero_resolution
    LIMITS["photoreal_min"]      = args.photoreal_min
    LIMITS["photoreal_attempts"] = args.photoreal_attempts
    print(f"[cost] max_packshots={LIMITS['max_packshots']}  "
          f"inspect={LIMITS['inspect_mode']}  "
          f"hero_variations={LIMITS['hero_variations']}  "
          f"skip_clutter={LIMITS['skip_clutter']}")
    print(f"[photoreal] {'ON' if LIMITS['photoreal'] else 'OFF'}  "
          f"model={args.hero_model}  res={LIMITS['hero_resolution']}  "
          f"target={LIMITS['photoreal_min']}%  attempts={LIMITS['photoreal_attempts']}")

    # Quality-gated reuse: scan the project for existing elements and skip
    # regenerating the ones that are already good (saves API credits on re-runs).
    global CACHE
    CACHE = AssetCache(args.project, enabled=not args.no_reuse,
                       min_score=args.reuse_min_score, rescore=args.rescore_existing)
    if not CACHE.enabled:
        print("[reuse] disabled (--no-reuse): every element will be regenerated")
    elif os.path.isdir(args.project):
        CACHE.scan()
    else:
        print("[reuse] new project - nothing to reuse yet")

    # Reset and configure the token/cost meter for this run.
    global METER
    METER = TokenMeter()
    METER.set_vision_model(args.vision_model or "gemini-3-1-pro")
    METER.set_image_model(getattr(args, "image_model", ""))
    METER.set_pro_resolution(args.hero_resolution)

    ID.init_project(args.project)
    cast_spec = ID.read_json(args.cast, DEFAULT_CAST) if args.cast else DEFAULT_CAST

    if args.dry_run or not args.recipes:
        client = DryRunClient()
        if not args.dry_run:
            print("[note] no --recipes given; running dry-run.")
    else:
        client = ComfyClient(args.comfy_url, args.recipes)
        client.qwen_model_path  = args.qwen_model_path
        client.qwen_mmproj_path = args.qwen_mmproj_path
        client.vision_backend   = args.vision_backend
        client.vision_model     = args.vision_model
        client.api_token   = args.api_token
        client.image_model = args.image_model
        client.hero_model  = args.hero_model
        # Override director recipe path if explicitly specified
        if args.director_recipe:
            client.director_recipe = args.director_recipe

    if 1 in phases:
        stage_cast(args.project, cast_spec, client)
        print(f"cast locked ({client.label})")

    frames = sorted(f for f in glob.glob(os.path.join(args.frames, "*"))
                    if f.lower().endswith(IMAGE_EXTS))
    if not frames:
        sys.exit(f"no screenshots found in {args.frames}")
    if args.scene:
        frames = [frames[args.scene - 1]]
        scene_nums = [args.scene]
    else:
        scene_nums = list(range(1, len(frames) + 1))

    # Director mode: use the full ComfyUI AI-Director + LTX-2.3 workflow for Phase 4.
    # The GUI workflow (open in browser) controls all quality knobs; Python only
    # loops segments and patches the frames folder + segment index.
    director_frames = args.director_frames
    if director_frames:
        print(f"[director mode] frames folder: {director_frames}")

    prev_master = ""
    scene_phases = phases - {5}   # phases 1-4 run per scene; 5 is post-loop
    for n, src in zip(scene_nums, frames):
        result = run_scene(args.project, n, src, client, phases=scene_phases,
                           _kokoro_voice=args.kokoro_voice, prev_master=prev_master,
                           director_frames=director_frames)
        prev_master = result.get("master", "")

    if 5 in phases:
        print("\n=== PHASE 5 — COMPOSE FINAL FILM ===")
        out_film = args.output_film or os.path.join(args.project, "final_film.mp4")
        stage_compose(args.project, out_path=out_film,
                      mode=args.compose_mode,
                      crossfade_secs=args.crossfade_secs,
                      clip_duration=args.clip_duration)

    print(f"\nDONE. Identity container at: {args.project}")
    print(f"Index: {ID.index_path(args.project)}")
    if CACHE.enabled:
        print(CACHE.summary())
    print(METER.summary())


if __name__ == "__main__":
    main()
