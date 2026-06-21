"""Bridge node: feed one segment's prompt/image/save-name into a video graph.

Wire its outputs into your LTXV (or any) video workflow:
  positive_prompt  -> CLIPTextEncode (positive) `text` input
  negative_prompt  -> CLIPTextEncode (negative) `text` input
  start_image_path -> a load-image-by-path node feeding the I2V image input
  save_prefix      -> SaveVideo `filename_prefix` input
Then just change `segment_index` and re-queue for each shot.
"""


class SegmentPromptPicker:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt_manifest": ("PROMPT_MANIFEST",),
                "segment_index": ("INT", {"default": 1, "min": 1, "max": 9999}),
                "save_prefix_base": ("STRING", {"default": "tour/shot"}),
            },
            "optional": {
                # Lets the picker resolve the start frame's image file path.
                "classified_path_frames": ("CLASSIFIED_PATH_FRAMES",),
                # Folder of cleaned stills, if different from the original frames.
                "stills_folder_override": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive_prompt", "negative_prompt",
                    "start_image_path", "end_image_path", "save_prefix", "info")
    FUNCTION = "pick"
    CATEGORY = "AI Director/Prompts"

    def _resolve(self, frames, ref_id, stills_base):
        import os
        path = ""
        if frames:
            for f in frames:
                if f.get("frame_id") == ref_id:
                    path = f.get("image_path", "")
                    break
        if stills_base and ref_id:
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                cand = os.path.join(stills_base, ref_id + ext)
                if os.path.exists(cand):
                    path = cand
                    break
        return path

    def pick(self, prompt_manifest, segment_index, save_prefix_base,
             classified_path_frames=None, stills_folder_override=""):
        manifest = prompt_manifest or []
        if not manifest:
            return ("", "", "", "", f"{save_prefix_base}_00",
                    "ERROR: empty prompt_manifest. Run the Prompt Compiler first.")

        idx = max(1, min(segment_index, len(manifest)))
        item = manifest[idx - 1]

        positive = item.get("prompt", "")
        negative = item.get("negative_prompt", "")
        start_ref = item.get("start_reference_frame_id", "")
        end_ref = item.get("end_reference_frame_id", "")
        stills_base = stills_folder_override.strip()

        start_path = self._resolve(classified_path_frames, start_ref, stills_base)
        end_path = self._resolve(classified_path_frames, end_ref, stills_base)

        save_prefix = f"{save_prefix_base}_{item.get('segment_id', idx):02d}"

        info = (
            f"Clip {item.get('segment_id', idx)}/{len(manifest)}  "
            f"[{item.get('time_range', '')}]  status={item.get('readiness_status', '?')}\n"
            f"START frame: {start_ref}  ->  {start_path or '(unresolved)'}\n"
            f"END frame:   {end_ref}  ->  {end_path or '(unresolved)'}\n"
            f"Save prefix: {save_prefix}"
        )
        return (positive, negative, start_path, end_path, save_prefix, info)
