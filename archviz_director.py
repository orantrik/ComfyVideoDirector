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

# 64x64 white PNG used as a placeholder asset in dry-run. A 1x1 image makes some
# LoadImage builds crash inside PyAV, so we keep it comfortably sized + valid.
_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAfElEQVR4nNXOQREAIADDsFL/no"
    "cIHlyjIGcbZRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIn"
    "cRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncf4OvLpyqgN9ZS"
    "iDcwAAAABJRU5ErkJggg=="
)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


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
        self.nb_nodes     = {"prompt": "3", "image_1": "1", "image_2": "2", "save": "4"}
        # API-node auth + model selection (set from the GUI / CLI).
        #   api_token   -> sent as extra_data.api_key_comfy_org so Gemini/NanoBanana
        #                  can authenticate without a logged-in ComfyOrg session.
        #   image_model -> patched into the GeminiNanoBanana2V2 'model' input.
        self.api_token   = ""
        self.image_model = "Nano Banana 2 (Gemini 3.1 Flash Image)"
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
        up = self.api.upload_image(self.url, image_path)
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
                return self.api.run_graph(self.url, g, out_path, extra_data=self._extra())
            except RuntimeError as e:
                if "did not generate an image" in str(e) and attempt < attempts:
                    last_err = e
                    print(f"    [retry {attempt}/{attempts}] Gemini returned no image; "
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
            return self.api.run_recipe_audio(self.url, self.f5_recipe, patches, out_path,
                                             extra_data=self._extra())
        else:
            # Kokoro: just text + voice preset.
            patches = {
                self.kokoro_nodes["voice_node"]: {"speaker_name": voice or self.kokoro_voice},
                self.kokoro_nodes["text_node"]:  {"text": text},
                self.kokoro_nodes["save"]:       {"filename_prefix": prefix},
            }
            return self.api.run_recipe_audio(self.url, self.kokoro_recipe, patches, out_path,
                                             extra_data=self._extra())

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
    furn = client.analyze(P.ANALYSIS_FURNITURE, src)
    objs = client.analyze(P.ANALYSIS_OBJECTS, src)
    space = client.analyze(P.ANALYSIS_SPACE, src)
    ID.write_text(os.path.join(d, "identity", "furniture", "_list.txt"), furn)
    ID.write_text(os.path.join(d, "identity", "objects", "_list.txt"), objs)
    ID.write_json(os.path.join(d, "identity", "space.json"), {"description": space})
    return parse_items(furn), parse_items(objs), space


def stage_packshots(root, n, furniture, objects, client):
    # The source screenshot anchors object identity (the model must extract the
    # real item from the real room, not invent a generic one).
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    for kind, items, dirfn in (("furniture", furniture, ID.furniture_item_dir),
                               ("objects", objects, ID.object_item_dir)):
        for it in items:
            idir = dirfn(root, n, it["id"])
            ID.write_text(os.path.join(idir, "desc.txt"),
                          f"{it['name']} | {it['desc']} | {it['location']}")
            packshot = os.path.join(idir, "packshot_4view.png")
            client.generate(P.fill(P.GEN_PACKSHOT_4VIEW,
                                   desc=f"{it['name']}, {it['desc']} ({it['location']})"),
                            [src], packshot)
            ID.register_scene_item(root, n, kind, it["id"], {
                "name": it["name"], "desc_path": os.path.join(idir, "desc.txt"),
                "packshot": packshot, "location": it["location"]})


def stage_space_map(root, n, space, client):
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    out = os.path.join(ID.scene_dir(root, n), "identity", "space_map.png")
    client.generate(P.fill(P.GEN_SPACE_MAP, desc=space), [src], out)
    return out


def stage_stabilized_space(root, n, space, client):
    """Stage D: generate the clean empty-space hero conditioned on the real
    screenshot (ground truth) + all packshots."""
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    refs = [src] + ID.reference_images(root, n)  # screenshot first, then packshots
    out = os.path.join(ID.scene_dir(root, n), "renders", "empty_space_stabilized.png")
    client.generate(P.fill(P.GEN_EMPTY_SPACE_STABILIZED, space=space), refs, out)
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
        client.generate(P.fill(P.GEN_CHARACTER_SHEET, n=7, desc=sp["desc"]), [], sheet)
        client.generate(P.fill(P.GEN_PORTRAIT, desc=sp["desc"]), [], portrait)
        for i, garment in enumerate(sp.get("clothes", []), 1):
            cp = os.path.join(sdir, "clothes_packshots", f"garment_{i:02d}.png")
            client.generate(P.fill(P.GEN_CLOTHES_PACKSHOT, desc=garment), [], cp)
        ID.register_cast(root, "spokesman", "spokesman",
                         {"desc_path": os.path.join(sdir, "desc.txt"),
                          "sheet": sheet, "portrait": portrait})
    for actor in cast_spec.get("actors", []):
        aid = actor["id"]
        adir = os.path.join(ID.cast_dir(root), "actors", aid)
        ID.write_text(os.path.join(adir, "desc.txt"), actor["desc"])
        sheet = os.path.join(adir, "sheet.png")
        portrait = os.path.join(adir, "portrait.png")
        client.generate(P.fill(P.GEN_CHARACTER_SHEET, n=5, desc=actor["desc"]), [], sheet)
        client.generate(P.fill(P.GEN_PORTRAIT, desc=actor["desc"]), [], portrait)
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
def _audio_duration_secs(wav_path):
    """Read WAV header to get duration (seconds). Falls back to 8.0 on parse error."""
    try:
        with open(wav_path, "rb") as fh:
            data = fh.read(44)
        # bytes 24-27: sample rate  bytes 34-35: bits/sample  bytes 40-43: data chunk size
        sample_rate  = struct.unpack_from("<I", data, 24)[0]
        bits_per_smp = struct.unpack_from("<H", data, 34)[0]
        channels     = struct.unpack_from("<H", data, 22)[0]
        data_size    = struct.unpack_from("<I", data, 40)[0]
        if sample_rate and bits_per_smp and channels:
            return data_size / (sample_rate * channels * (bits_per_smp // 8))
    except Exception:
        pass
    return 8.0   # safe default


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
    """Stage G (part 1): Qwen3-VL extracts x/y/area coordinates for every element."""
    raw = client.analyze(P.ANALYSIS_COORDINATES, src)
    try:
        coords = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        coords = json.loads(m.group(0)) if m else []
    path = os.path.join(ID.scene_dir(root, n), "coordinates.json")
    ID.write_json(path, coords)
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


def stage_hero_composite(root, n, space, coords, client, prev_master=""):
    """Stage I: generate 4 hero-composite variations (space + cast + coords).

    `prev_master` provides the incoming-scene context for cross-scene continuity.
    """
    # Screenshot first (space ground truth), then character sheets + packshots.
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    refs = [src] + list(ID.reference_images(root, n))
    coords_str = json.dumps(coords)
    prev_ctx = (f" Arriving from: {prev_master[:300]}.") if prev_master.strip() else ""
    prompt = P.fill(P.GEN_HERO_COMPOSITE, space=space, coords=coords_str) + prev_ctx
    variations = []
    for v in range(1, 5):
        out = os.path.join(ID.scene_dir(root, n), "renders", f"hero_v{v}.png")
        client.generate(prompt, list(refs), out)   # fresh copy of refs each call
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


def stage_inspect(root, n, furniture, objects, variations, client):
    """Stage K: score every element in each hero variation; gate ≥90; emit report.json."""
    idx = ID.load_index(root)
    cast = idx.get("cast", {})
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

    var_results = []
    best_idx, best_avg = 0, -1.0
    for vi, hero_path in enumerate(variations, 1):
        scores, hallu_all = [], []
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

    if 2 in phases:
        coords = stage_coordinates(root, n, src, client)
        print(f"  coordinates: {len(coords)} elements (Stage G)")
        master, _ = stage_master_prompt(root, n, furniture, objects, space, client,
                                        prev_master=prev_master)
        print("  master prompt + audio prompt done (Stage G)"
              + (f" [with scene {n-1} context]" if prev_master else ""))
    else:
        master = ID.read_text(
            os.path.join(ID.scene_dir(root, n), "prompts", "master_prompt.txt"), "")

    if 3 in phases:
        stage_audio(root, n, client, kokoro_voice=_kokoro_voice)

    if 2 in phases:
        variations = stage_hero_composite(root, n, space, coords, client,
                                          prev_master=prev_master)
        print(f"  hero composite: {len(variations)} variations (Stage I)")
        stage_reconcile(root, n, furniture, objects, coords, client)
        print("  prompts reconciled (Stage J)")
        report, best_idx = stage_inspect(root, n, furniture, objects, variations, client)
        vr = report["variations"][best_idx]
        gate = "PASS" if vr["passes_gate"] else f"best={vr['average']:.0f}%"
        print(f"  inspector done [{gate}] (Stage K)")
        stage_lock_hero(root, n, variations, best_idx, report)

    if 4 in phases:
        stage_video(root, n, client, director_frames=director_frames)

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
        client.api_token   = args.api_token
        client.image_model = args.image_model
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


if __name__ == "__main__":
    main()
