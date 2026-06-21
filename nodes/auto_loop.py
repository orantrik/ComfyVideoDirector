"""Self-driving autoregressive tour loop for ComfyUI.

Run these three nodes with ComfyUI's **Auto Queue (change)** turned on and the
graph renders the whole tour by itself:

  AIDirectorAutoLoop   - source node. Each run it reads loop state and emits the
                         current clip's prompt + start image (the previous clip's
                         generated last frame) + save name. It does NOT advance;
                         it just reflects the current index.
  AIDirectorStoreFrame - end node. Saves the LAST decoded frame of the clip to
                         the path AutoLoop reads next run, and advances the index.
                         Also outputs that last frame (wire it to Qwen3-VL).
  AIDirectorStoreCaption - end node. Stores Qwen3-VL's description of the last
                         frame so the NEXT clip's prompt can continue from what
                         actually happened on screen.

State lives in a small json keyed by loop_id, so the three nodes share it across
queue runs. AutoLoop.IS_CHANGED returns the current index: it changes while the
tour advances (so Auto Queue keeps firing) and goes stable when done (so the loop
stops on its own).
"""

import os
import json
import time

try:
    import folder_paths
    def _base():
        return folder_paths.get_temp_directory()
except Exception:
    def _base():
        import tempfile
        return tempfile.gettempdir()


def _dir():
    d = os.path.join(_base(), "aidir_loops")
    os.makedirs(d, exist_ok=True)
    return d


def _path(loop_id):
    return os.path.join(_dir(), f"{loop_id or 'tour'}.json")


def _load(loop_id):
    p = _path(loop_id)
    if os.path.isfile(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {"index": 0, "last_frame_path": "", "last_caption": ""}


def _save(loop_id, st):
    json.dump(st, open(_path(loop_id), "w", encoding="utf-8"))


def _compose(idx, presenter, direction, style, continue_from, audio):
    pres = (presenter.strip() + " ") if presenter.strip() else ""
    if idx == 0:
        opening = "The opening shot of the tour:"
    else:
        cf = (" — on screen now: " + continue_from.strip()) if continue_from.strip() else ""
        opening = ("Continuing the exact same unbroken cinematic take, picking up "
                   "precisely where the previous shot ended" + cf + ",")
    visual = (
        f"VISUAL:\n{opening} a photorealistic continuous tracking shot, one smooth "
        f"unbroken take with no cut. {pres}She {direction}. The modern luxury "
        f"architecture is revealed in crisp photorealistic detail with warm natural "
        f"light, shallow depth of field keeping her in sharp focus, subtle film "
        f"grain and gentle motion blur. {style.strip()}"
    )
    aud = audio.strip() or ("Natural high-fidelity ambient sound: a soft breeze, "
                            "gentle footsteps, an upscale neighborhood hum.")
    return visual + "\n\nAUDIO:\n" + aud


DEFAULT_NEG = ("blurry, distorted, warped geometry, jump cut, flicker, low-res, "
               "cartoon, 3d render look, duplicated person, frozen still image, "
               "static overlay, oversaturated")


class AIDirectorAutoLoop:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "loop_id": ("STRING", {"default": "tour"}),
                "clip_count": ("INT", {"default": 5, "min": 1, "max": 999}),
                "seed_image_path": ("STRING", {"default": "", "placeholder": "abs path to the FIRST start frame (e.g. last frame of your good video)"}),
                "presenter": ("STRING", {"default": "", "multiline": True, "placeholder": "the recurring woman, reused every clip"}),
                "audio": ("STRING", {"default": "", "multiline": True}),
                "style": ("STRING", {"default": "warm golden-hour cinematography, photorealistic", "multiline": True}),
                "directions": ("STRING", {"default": "", "multiline": True, "placeholder": "ONE line per clip: what she does / where she goes\nwalks through the glass entrance into the lobby\ncrosses the lobby toward the elevators\nsteps out onto the sky-lounge amenity deck"}),
                "negative": ("STRING", {"default": DEFAULT_NEG, "multiline": True}),
            },
            "optional": {
                "reset": ("BOOLEAN", {"default": False}),
                # Optional + last so adding it never shifts saved widget order.
                "clip_seconds": ("INT", {"default": 8, "min": 1, "max": 60}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "INT", "FLOAT", "BOOLEAN")
    RETURN_NAMES = ("positive", "negative", "start_image_path", "save_prefix",
                    "index", "audio_start_seconds", "done")
    FUNCTION = "run"
    CATEGORY = "AI Director/Loop"

    @classmethod
    def IS_CHANGED(cls, loop_id, reset=False, **kw):
        # Re-run while the index advances; stable (no re-run) when finished.
        if reset:
            return time.time()
        return _load(loop_id).get("index", 0)

    def run(self, loop_id, clip_count, seed_image_path, presenter,
            audio, style, directions, negative, reset=False, clip_seconds=8):
        if reset:
            _save(loop_id, {"index": 0, "last_frame_path": "", "last_caption": ""})
        st = _load(loop_id)
        idx = st.get("index", 0)
        print(f"[AI Director] Auto Loop READ: loop_id='{loop_id}' index={idx} "
              f"(state file: {_path(loop_id)})")
        done = idx >= clip_count

        if done:
            print(f"[AI Director] Tour '{loop_id}' complete: {clip_count} clips. "
                  "Turn off Auto Queue.")
            try:
                from comfy.model_management import InterruptProcessingException
                raise InterruptProcessingException()
            except ImportError:
                pass

        start = seed_image_path.strip() if idx == 0 else st.get("last_frame_path", "")
        dirs = [l.strip() for l in directions.splitlines() if l.strip()]
        direction = dirs[idx] if idx < len(dirs) else "continues the tour into the next space, presenting it"
        prompt = _compose(idx, presenter, direction, style,
                          st.get("last_caption", ""), audio)
        save_prefix = f"{loop_id}/clip_{idx + 1:02d}"
        audio_start = float(idx * max(1, clip_seconds))
        return (prompt, negative or DEFAULT_NEG, start, save_prefix, idx,
                audio_start, done)


class AIDirectorStoreFrame:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "loop_id": ("STRING", {"default": "tour"}),
            },
            "optional": {
                "advance": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("last_frame", "last_frame_path")
    FUNCTION = "store"
    CATEGORY = "AI Director/Loop"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kw):
        return time.time()

    def store(self, images, loop_id, advance=True):
        import numpy as np
        import torch
        from PIL import Image
        last = images[-1:]                       # [1,H,W,3]
        arr = (last[0].cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
        out = os.path.join(_dir(), f"{loop_id or 'tour'}_last.png")
        Image.fromarray(arr, "RGB").save(out)

        st = _load(loop_id)
        st["last_frame_path"] = out
        if advance:
            st["index"] = st.get("index", 0) + 1
        _save(loop_id, st)
        print(f"[AI Director] stored last frame -> {out}  (next index {st['index']})")
        return (last, out)


class AIDirectorStoreCaption:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "caption": ("STRING", {"forceInput": True}),
                "loop_id": ("STRING", {"default": "tour"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("caption",)
    FUNCTION = "store"
    CATEGORY = "AI Director/Loop"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kw):
        return time.time()

    def store(self, caption, loop_id):
        st = _load(loop_id)
        st["last_caption"] = (caption or "").strip()
        _save(loop_id, st)
        return (caption,)
