import os
from ..core.file_utils import parse_selected_files, list_images_in_folder, build_frames
from ..core.schemas import PATH_FRAME_CATEGORIES


def _parse_overrides(text):
    """Parse 'stem_or_filename:category' lines into a dict.

    Accepts both full filenames and bare stems, with or without extension.
    Example lines:
        HighresScreenshot00001:start_frame
        HighresScreenshot00002 : exterior_approach
        HighresScreenshot00003:entrance_threshold
    """
    result = {}
    if not text or not text.strip():
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        parts = line.split(":", 1)
        key = os.path.splitext(parts[0].strip())[0]   # strip extension if present
        val = parts[1].strip().lower()
        if val in PATH_FRAME_CATEGORIES:
            result[key] = val
        else:
            result[key] = ("unknown", f"unknown category '{val}'")
    return result


def _parse_location_notes(text):
    """Parse 'stem:location description' lines into a dict."""
    result = {}
    if not text or not text.strip():
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        parts = line.split(":", 1)
        key = os.path.splitext(parts[0].strip())[0]
        result[key] = parts[1].strip()
    return result


class UnrealFrameIntake:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": ("DIRECTOR_PROJECT",),
                "naming_mode": (
                    ["gui_order", "ordered_filename", "manual_notes", "auto_guess"],
                    {"default": "auto_guess"},
                ),
            },
            "optional": {
                # Populated by the GUI file picker (JSON array). Also accepts a
                # newline/comma list of paths so the pipeline is usable pre-GUI.
                "selected_frame_files": ("STRING", {"default": "", "multiline": True}),
                "selected_frames_folder": ("STRING", {"default": ""}),
                # One line per frame: "HighresScreenshot00001:start_frame"
                "category_overrides": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": (
                        "HighresScreenshot00001:start_frame\n"
                        "HighresScreenshot00002:exterior_approach\n"
                        "HighresScreenshot00003:entrance_threshold\n"
                        "HighresScreenshot00004:lobby_reveal\n"
                        "HighresScreenshot00005:final_frame"
                    ),
                }),
                # Per-frame understanding (wire Qwen3-VL captions here, or type).
                # One caption per line, IN FRAME ORDER (line 1 = frame 1).
                "frame_descriptions": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": (
                        "One caption per line, in order (e.g. Qwen3-VL output):\n"
                        "wide exterior plaza, glass tower at golden hour\n"
                        "pushing toward the lit entrance canopy\n"
                        "inside the marble lobby atrium"
                    ),
                }),
                # One line per frame: "HighresScreenshot00001:Plaza level exterior, golden hour"
                "location_notes": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": (
                        "HighresScreenshot00001:exterior plaza at ground level\n"
                        "HighresScreenshot00002:entrance canopy and threshold\n"
                        "HighresScreenshot00003:main lobby atrium"
                    ),
                }),
                "manual_frame_notes": ("STRING", {"default": "", "multiline": True}),
            },
        }

    RETURN_TYPES = ("PATH_FRAMES", "STRING")
    RETURN_NAMES = ("path_frames", "intake_summary")
    FUNCTION = "intake"
    CATEGORY = "AI Director/Frames"

    def intake(self, project, naming_mode, selected_frame_files="",
               selected_frames_folder="", category_overrides="",
               frame_descriptions="", location_notes="", manual_frame_notes=""):
        warnings = []

        paths = parse_selected_files(selected_frame_files)
        source = "GUI/file selection"
        if not paths and selected_frames_folder:
            paths = list_images_in_folder(selected_frames_folder)
            source = f"folder scan ({selected_frames_folder})"

        if not paths:
            return ([], (
                "No frames found. Select Unreal screenshots in the GUI, or set "
                "selected_frames_folder to a folder of images."
            ))

        gui_ordered = naming_mode in ("gui_order", "auto_guess")
        if naming_mode == "ordered_filename":
            gui_ordered = False

        frames, build_warnings = build_frames(paths, gui_ordered=gui_ordered)
        warnings.extend(build_warnings)

        # Apply category overrides (stem or filename → category).
        cat_map = _parse_overrides(category_overrides)
        loc_map = _parse_location_notes(location_notes)
        override_applied = 0
        override_warnings = []

        for f in frames:
            stem = os.path.splitext(f["frame_id"])[0]
            if stem in cat_map:
                val = cat_map[stem]
                if isinstance(val, tuple):          # unknown category warning
                    override_warnings.append(f"{stem}: {val[1]}")
                    f["category"] = "unknown"
                else:
                    f["category"] = val
                    override_applied += 1
            if stem in loc_map:
                f["location"] = loc_map[stem]

        # Per-frame descriptions (Qwen3-VL captions or typed), one line per frame.
        desc_source = frame_descriptions if frame_descriptions.strip() else manual_frame_notes
        if desc_source.strip():
            notes = [n.strip() for n in desc_source.splitlines() if n.strip()]
            for f, note in zip(frames, notes):
                f["visual_description"] = note

        summary = [
            f"{len(frames)} frame(s) loaded from {source}.",
            f"Order source: {'filename numbers' if naming_mode == 'ordered_filename' else naming_mode}.",
        ]
        if override_applied:
            summary.append(f"{override_applied} category override(s) applied.")
        for w in override_warnings:
            summary.append(f"CATEGORY WARNING: {w}")
        for w in warnings:
            summary.append(f"WARNING: {w}")
        summary.append("Order: " + " -> ".join(f["frame_id"] for f in frames[:30]))
        if cat_map:
            summary.append("Categories set: " + ", ".join(
                f"{k}={v}" for k, v in cat_map.items() if not isinstance(v, tuple)
            ))

        return (frames, "\n".join(summary))
