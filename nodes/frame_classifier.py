from ..core.schemas import (
    CATEGORY_KEYWORDS, CATEGORY_PRIORITY, REQUIRED_CONTINUOUS_PATH_CATEGORIES,
)
from ..core.validation import check_required_categories


def _priority_rank(category):
    try:
        return CATEGORY_PRIORITY.index(category)
    except ValueError:
        return len(CATEGORY_PRIORITY) + 1


def _classify_by_filename(name):
    """Return (category, confident) for a filename/stem.

    A filename like ``008_amenity_reveal`` matches several categories
    (``amenity`` -> amenity_reveal, ``reveal`` -> lobby_reveal). The defining
    word is the one that appears *earliest* in the name. We therefore pick the
    category whose keyword occurs at the smallest index, tie-broken by the
    longest keyword, then by category priority. This avoids generic tokens like
    "reveal" or "skyline" overriding the specific intent of the frame.
    """
    low = name.lower()
    best = None  # (index, -len(keyword), priority_rank, category)
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            idx = low.find(kw)
            if idx != -1:
                cand = (idx, -len(kw), _priority_rank(category), category)
                if best is None or cand < best:
                    best = cand
    if best is None:
        return "unknown", False
    return best[3], True


class FrameClassifier:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "path_frames": ("PATH_FRAMES",),
                "classification_mode": (
                    ["filename_rules", "manual_notes", "llm_optional"],
                    {"default": "filename_rules"},
                ),
                "default_camera_height": ("STRING", {"default": "eye-level"}),
                "default_camera_speed": ("STRING", {"default": "slow walking pace"}),
                "default_lens_feel": ("STRING", {"default": "wide architectural lens, minimal distortion"}),
            },
        }

    RETURN_TYPES = ("CLASSIFIED_PATH_FRAMES", "STRING")
    RETURN_NAMES = ("classified_path_frames", "classification_summary")
    FUNCTION = "classify"
    CATEGORY = "AI Director/Frames"

    def classify(self, path_frames, classification_mode, default_camera_height,
                 default_camera_speed, default_lens_feel):
        frames = [dict(f) for f in path_frames]
        confident = 0
        needs_review = []

        for f in frames:
            # Respect a category already set by the GUI inspector.
            if f.get("category") and f["category"] != "unknown":
                confident += 1
            else:
                basis = f.get("file_name", "") + " " + f.get("visual_description", "")
                category, ok = _classify_by_filename(basis)
                f["category"] = category
                if ok:
                    confident += 1
                else:
                    needs_review.append(f["frame_id"])

            f.setdefault("camera_height", default_camera_height)
            f["camera_height"] = f.get("camera_height") or default_camera_height
            f["camera_speed"] = f.get("camera_speed") or default_camera_speed
            f["lens_feel"] = f.get("lens_feel") or default_lens_feel

        missing_required = check_required_categories(frames, REQUIRED_CONTINUOUS_PATH_CATEGORIES)

        summary = [
            f"{len(frames)} frames loaded.",
            f"{confident} frames classified confidently.",
            f"{len(needs_review)} frames need manual category confirmation"
            + (f": {', '.join(needs_review)}." if needs_review else "."),
        ]
        if missing_required:
            summary.append("Missing required categories: " + ", ".join(missing_required) + ".")
        else:
            summary.append("All required categories present.")

        return (frames, "\n".join(summary))
