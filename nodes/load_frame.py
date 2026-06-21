"""Load an image from an absolute path string (for the Segment Prompt Picker).

Core LoadImage can only pick from the input folder; this loads any path the
Director resolves (start_image_path / end_image_path).
"""

import os
import numpy as np
import torch
from PIL import Image, ImageOps


class AIDirectorLoadFrame:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_path": ("STRING", {"default": "", "forceInput": True}),
            },
            "optional": {
                "fallback_path": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("image", "mask", "resolved_path")
    FUNCTION = "load"
    CATEGORY = "AI Director/Frames"

    def load(self, image_path, fallback_path=""):
        path = (image_path or "").strip() or (fallback_path or "").strip()
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(
                f"AIDirectorLoadFrame: image not found at {path!r}. "
                "Wire the Picker's start_image_path/end_image_path, or make sure "
                "Frame Intake resolved real file paths."
            )

        img = Image.open(path)
        img = ImageOps.exif_transpose(img)

        if "A" in img.getbands():
            rgba = img.convert("RGBA")
            rgb = np.array(rgba.convert("RGB")).astype(np.float32) / 255.0
            alpha = np.array(rgba.getchannel("A")).astype(np.float32) / 255.0
            mask = 1.0 - alpha  # ComfyUI convention: white = masked
        else:
            rgb = np.array(img.convert("RGB")).astype(np.float32) / 255.0
            mask = np.zeros(rgb.shape[:2], dtype=np.float32)

        image_t = torch.from_numpy(rgb)[None, ]
        mask_t = torch.from_numpy(mask)[None, ]
        return (image_t, mask_t, path)
