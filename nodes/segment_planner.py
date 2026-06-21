from ..core.timeline import build_segment_time_ranges, format_timecode
from ..core.schemas import DEFAULT_SEAM_RULE


def _desc_of(frame):
    return (
        frame.get("visual_description")
        or frame.get("location")
        or frame.get("category", "frame").replace("_", " ")
    )


def _plan_keyframe_pairs(project, frames, motion_notes, loop_back):
    """One 8s clip per consecutive frame pair. End frame of clip i is the start
    frame of clip i+1 -> seamless continuous shot. N frames -> N-1 clips
    (or N clips if loop_back)."""
    seg_dur = project.get("segment_duration_seconds", 8)
    N = len(frames)
    pairs = [(i, i + 1) for i in range(N - 1)]
    if loop_back and N > 1:
        pairs.append((N - 1, 0))

    motion_lines = [l.strip() for l in (motion_notes or "").splitlines() if l.strip()]
    segments = []
    t = 0
    for sidx, (a, b) in enumerate(pairs):
        sa, sb = frames[a], frames[b]
        ts, te = format_timecode(t), format_timecode(t + seg_dur)
        t += seg_dur
        start_desc, end_desc = _desc_of(sa), _desc_of(sb)
        motion = (
            motion_lines[sidx] if sidx < len(motion_lines)
            else f"one continuous {sa.get('camera_speed', 'slow')} camera move"
        )
        segments.append({
            "segment_id": sidx + 1,
            "clip_id": sidx + 1,
            "time_start": ts,
            "time_end": te,
            "duration_seconds": seg_dur,
            "start_reference_frame_id": sa["frame_id"],
            "end_reference_frame_id": sb["frame_id"],
            "start_desc": start_desc,
            "end_desc": end_desc,
            "location": sa.get("location") or start_desc,
            "movement": motion,
            "visual_action": (
                f"Begin exactly on '{sa['frame_id']}' ({start_desc}) and move so "
                f"the clip ends exactly on '{sb['frame_id']}' ({end_desc}). That "
                f"end frame is the first frame of the next clip."
            ),
            "transition_rule": DEFAULT_SEAM_RULE,
            "seam_rule_previous": "" if sidx == 0 else "Open on the previous clip's exact end frame.",
            "seam_rule_next": "Close on the exact composition that the next clip opens on.",
            "continuity_constraints": [
                "open on start keyframe", "close on end keyframe",
                "one unbroken move", "consistent lighting/lens/color", "no cut",
            ],
            "prompt": "",
            "negative_prompt": "",
            "validation_criteria": [
                f"First frame matches keyframe '{sa['frame_id']}'",
                f"Last frame matches keyframe '{sb['frame_id']}'",
                "Motion is one continuous move",
                "Lighting/color continuous with neighbors",
            ],
            "readiness_status": "ready",
            "missing_elements": [],
            "risk_level": "low",
        })

    summary = [
        f"Agnostic one-shot plan: {N} frames -> {len(segments)} clips "
        f"({seg_dur}s each, total {len(segments) * seg_dur}s).",
        "Each clip's END frame is the next clip's START frame (seamless).",
    ]
    if N < 2:
        summary = ["Need at least 2 frames for keyframe-pair mode. Upload more frames."]
    return segments, "\n".join(summary)


def _gap_boundary_indices(frames):
    """Order positions i where a continuity gap exists between frame i and i+1."""
    cats = [f.get("category", "unknown") for f in frames]
    boundaries = {}
    for i in range(len(cats) - 1):
        cur, nxt = cats[i], cats[i + 1]
        miss = None
        if cur in ("exterior_approach", "arrival_plaza") and nxt == "lobby_reveal":
            miss = "entrance_threshold"
        elif cur.startswith("lobby") and nxt.startswith("amenity"):
            miss = "interior_transition or corridor_transition"
        elif cur.startswith("amenity") and nxt.startswith("apartment"):
            miss = "corridor/elevator/apartment_entry"
        elif cur.startswith("apartment") and nxt.startswith("rooftop"):
            miss = "elevator_transition or rooftop_arrival"
        if miss:
            boundaries[i] = miss
    return boundaries


class SegmentPlanner:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": ("DIRECTOR_PROJECT",),
                "classified_path_frames": ("CLASSIFIED_PATH_FRAMES",),
                "missing_frame_report": ("MISSING_FRAME_REPORT",),
                "allow_placeholders": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                # keyframe_pairs mode: one motion line per transition (frame i->i+1).
                "motion_notes": ("STRING", {"default": "", "multiline": True}),
                "loop_back": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("CONTINUOUS_SEGMENTS", "STRING")
    RETURN_NAMES = ("continuous_segments", "segment_plan_summary")
    FUNCTION = "plan"
    CATEGORY = "AI Director/Planning"

    def plan(self, project, classified_path_frames, missing_frame_report,
             allow_placeholders, motion_notes="", loop_back=False):
        frames = sorted(classified_path_frames or [], key=lambda f: f.get("order", 9999))
        N = len(frames)

        # Agnostic one-shot: derive clips from frame pairs.
        if project.get("shot_mode", "fixed_grid") == "keyframe_pairs":
            return _plan_keyframe_pairs(project, frames, motion_notes, loop_back)
        ranges = build_segment_time_ranges(
            project.get("total_duration_seconds", 240),
            project.get("segment_duration_seconds", 8),
        )
        S = len(ranges)
        boundaries = _gap_boundary_indices(frames)

        segments = []
        blocked, needs_review, ready = [], [], []

        for i, tr in enumerate(ranges):
            seg_id = tr["segment_id"]

            if N == 0:
                start_f = end_f = None
                start_idx = end_idx = -1
            else:
                start_idx = round((i / S) * (N - 1))
                end_idx = round(((i + 1) / S) * (N - 1))
                end_idx = min(end_idx, N - 1)
                if end_idx == start_idx and start_idx < N - 1:
                    end_idx = start_idx + 1
                start_f = frames[start_idx]
                end_f = frames[end_idx]

            crosses = [b for b in boundaries if start_idx <= b < end_idx] if N else []
            unknown_here = start_f and start_f.get("category") == "unknown"

            status = "ready"
            risk = "low"
            missing_elements = []
            if N == 0:
                status, risk = "blocked", "blocking"
                missing_elements = ["no keyframes uploaded"]
            elif crosses:
                missing_elements = [boundaries[b] for b in crosses]
                if allow_placeholders:
                    status, risk = "needs_review", "high"
                else:
                    status, risk = "blocked", "blocking"
            elif unknown_here:
                status, risk = "needs_review", "medium"
                missing_elements = ["unclassified reference frame"]

            if status == "blocked":
                blocked.append(seg_id)
            elif status == "needs_review":
                needs_review.append(seg_id)
            else:
                ready.append(seg_id)

            location = (
                start_f.get("location") or
                start_f.get("category", "").replace("_", " ")
            ) if start_f else ""
            movement = (
                f"{start_f.get('movement_type', 'dolly forward')} at a "
                f"{start_f.get('camera_speed', 'slow walking pace')}, "
                f"{start_f.get('camera_direction', 'forward')} direction"
            ) if start_f else "hold"

            if start_f and end_f:
                end_cat = end_f.get("category", "unknown")
                end_loc = end_f.get("location") or end_cat.replace("_", " ")
                start_loc = start_f.get("location") or start_f.get("category", "").replace("_", " ")
                if end_cat != "unknown":
                    visual_action = (
                        f"Continue the camera move from {start_loc} toward "
                        f"{end_loc}, revealing the "
                        f"{end_cat.replace('_', ' ')}."
                    )
                else:
                    visual_action = (
                        f"Continue forward from reference frame "
                        f"'{start_f['frame_id']}' toward '{end_f['frame_id']}'. "
                        f"Assign a category override in Frame Intake to generate "
                        f"a more specific action description."
                    )
            else:
                visual_action = "Awaiting keyframes."

            segments.append({
                "segment_id": seg_id,
                "clip_id": seg_id,
                "time_start": tr["time_start"],
                "time_end": tr["time_end"],
                "duration_seconds": tr["duration_seconds"],
                "start_reference_frame_id": start_f["frame_id"] if start_f else "",
                "end_reference_frame_id": end_f["frame_id"] if end_f else "",
                "location": location,
                "movement": movement,
                "visual_action": visual_action,
                "transition_rule": DEFAULT_SEAM_RULE,
                "seam_rule_previous": "" if i == 0 else DEFAULT_SEAM_RULE,
                "seam_rule_next": "" if i == S - 1 else DEFAULT_SEAM_RULE,
                "continuity_constraints": [
                    "same camera height", "same lens feel", "continuous lighting",
                    "consistent architecture", "no jump cut",
                ],
                "prompt": "",            # filled by Prompt Compiler
                "negative_prompt": "",   # filled by Prompt Compiler
                "validation_criteria": [
                    "First frame continues from previous segment",
                    "Camera movement matches the planned motion",
                    "Lighting remains continuous",
                    "Architecture matches the Unreal reference frames",
                    f"Last frame prepares for {end_f.get('category','next') if end_f else 'next'} ",
                ],
                "readiness_status": status,
                "missing_elements": missing_elements,
                "risk_level": risk,
            })

        summary = [
            f"Planned {S} segments ({project.get('total_duration_seconds',240)}s / "
            f"{project.get('segment_duration_seconds',8)}s).",
            f"Ready: {len(ready)}  Needs review: {len(needs_review)}  Blocked: {len(blocked)}",
        ]
        if blocked:
            summary.append("Blocked segments: " + ", ".join(map(str, blocked)))
        if needs_review:
            summary.append("Needs review: " + ", ".join(map(str, needs_review)))
        if not blocked and not needs_review and N:
            summary.append("All segments ready. Prompt manifest can be compiled and exported.")

        return (segments, "\n".join(summary))
