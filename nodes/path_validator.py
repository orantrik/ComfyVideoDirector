from ..core.schemas import REQUIRED_CONTINUOUS_PATH_CATEGORIES
from ..core.validation import check_required_categories, detect_path_gaps


class ContinuousPathValidator:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": ("DIRECTOR_PROJECT",),
                "classified_path_frames": ("CLASSIFIED_PATH_FRAMES",),
                "strictness": (["loose", "normal", "strict"], {"default": "normal"}),
            },
        }

    RETURN_TYPES = ("MISSING_FRAME_REPORT", "STRING", "BOOLEAN")
    RETURN_NAMES = ("missing_frame_report", "missing_report_text", "continuous_shot_ready")
    FUNCTION = "validate"
    CATEGORY = "AI Director/Validation"

    def validate(self, project, classified_path_frames, strictness):
        frames = classified_path_frames or []

        # Agnostic mode: no category requirements. Only need >= 2 ordered frames.
        if project.get("content_mode") == "agnostic" or project.get("shot_mode") == "keyframe_pairs":
            ok = len(frames) >= 2
            n_clips = max(0, len(frames) - 1)
            report = {
                "continuous_shot_ready": ok,
                "uploaded_keyframes": len(frames),
                "missing_required_categories": [],
                "missing_path_frames": [],
                "missing_motion_details": [],
                "blocked_segments": [],
                "warnings": [] if ok else ["Need at least 2 frames."],
                "recommendations": [],
            }
            text = (
                f"Agnostic one-shot status: {'READY' if ok else 'NOT READY'}\n"
                f"Frames: {len(frames)}  ->  {n_clips} clip(s)\n"
                + ("Each clip's end frame = next clip's start frame."
                   if ok else "Upload at least 2 ordered frames.")
            )
            return (report, text, ok)

        missing_required = check_required_categories(frames, REQUIRED_CONTINUOUS_PATH_CATEGORIES)
        gaps = detect_path_gaps(frames)

        warnings = []
        if len(frames) < 8:
            warnings.append(
                f"Only {len(frames)} keyframes uploaded; 12-20 are recommended "
                "for a smooth continuous shot."
            )

        # Readiness depends on strictness.
        if strictness == "loose":
            ready = len(missing_required) == 0
        elif strictness == "strict":
            ready = len(missing_required) == 0 and len(gaps) == 0 and len(frames) >= 12
        else:  # normal
            ready = len(missing_required) == 0 and len(gaps) == 0

        recommendations = []
        for cat in missing_required:
            recommendations.append(f"Capture and upload a '{cat}' frame in Unreal.")
        for g in gaps:
            recommendations.append(
                f"Add a {g['missing']} frame between {g['between'][0]} and {g['between'][1]}."
            )

        report = {
            "continuous_shot_ready": ready,
            "uploaded_keyframes": len(frames),
            "missing_required_categories": missing_required,
            "missing_path_frames": gaps,
            "missing_motion_details": [],
            "blocked_segments": [],  # filled by the planner
            "warnings": warnings,
            "recommendations": recommendations,
        }

        # Human-readable text -------------------------------------------- #
        lines = []
        lines.append("Continuous shot status: " + ("READY" if ready else "NOT READY"))
        lines.append(f"Uploaded keyframes: {len(frames)}")
        if missing_required:
            lines.append("Missing required categories:")
            lines.extend(f"- {c}" for c in missing_required)
        else:
            lines.append("Required categories: all present.")
        if gaps:
            lines.append("Missing path frames:")
            for g in gaps:
                lines.append(
                    f"- Between {g['between'][0]} and {g['between'][1]}: "
                    f"add {g['missing']} ({g['reason']})"
                )
        for w in warnings:
            lines.append(f"WARNING: {w}")

        return (report, "\n".join(lines), ready)
