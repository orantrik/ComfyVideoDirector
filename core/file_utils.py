"""Helpers for turning GUI/folder selections into ordered frame records."""

import os
import re
import json

from .schemas import IMAGE_EXTENSIONS, new_path_frame


def parse_selected_files(selected_frame_files):
    """Accept a JSON array, newline list, or comma list of file paths/names.

    Returns a list of path-or-name strings (order preserved).
    """
    if not selected_frame_files:
        return []
    if isinstance(selected_frame_files, (list, tuple)):
        return [str(x).strip() for x in selected_frame_files if str(x).strip()]

    text = str(selected_frame_files).strip()
    if not text:
        return []

    # Try JSON first (the GUI stores a JSON array).
    try:
        data = json.loads(text)
        if isinstance(data, list):
            out = []
            for item in data:
                if isinstance(item, dict):
                    out.append(item.get("path") or item.get("name") or item.get("filename") or "")
                else:
                    out.append(str(item))
            return [s.strip() for s in out if s and s.strip()]
    except (ValueError, TypeError):
        pass

    # Fall back to newline / comma separated.
    sep = "\n" if "\n" in text else ","
    return [p.strip() for p in text.split(sep) if p.strip()]


def list_images_in_folder(folder):
    if not folder or not os.path.isdir(folder):
        return []
    names = [
        n for n in os.listdir(folder)
        if n.lower().endswith(IMAGE_EXTENSIONS)
    ]
    names.sort()
    return [os.path.join(folder, n) for n in names]


def _leading_order(stem):
    m = re.match(r"^\s*(\d+)", stem)
    return int(m.group(1)) if m else None


def build_frames(paths, gui_ordered=True):
    """Create ordered PathFrame dicts from a list of file paths.

    Returns (frames, warnings).
    """
    warnings = []
    frames = []
    has_numbers = False

    for idx, p in enumerate(paths):
        file_name = os.path.basename(p)
        stem = os.path.splitext(file_name)[0]
        lead = _leading_order(stem)
        if lead is not None:
            has_numbers = True
        order = lead if (lead is not None and not gui_ordered) else (idx + 1)
        frames.append(new_path_frame(
            frame_id=stem,
            order=order,
            image_path=p,
            file_name=file_name,
            category="unknown",
        ))

    if not gui_ordered and not has_numbers:
        warnings.append(
            "No leading numbers found in filenames and no GUI order given; "
            "frames were sorted alphabetically. Reorder them in the GUI or "
            "prefix filenames like 001_, 002_."
        )

    frames.sort(key=lambda f: f["order"])
    # Re-link previous/next ids after sorting.
    for i, f in enumerate(frames):
        f["previous_frame_id"] = frames[i - 1]["frame_id"] if i > 0 else ""
        f["next_frame_id"] = frames[i + 1]["frame_id"] if i < len(frames) - 1 else ""
    return frames, warnings
