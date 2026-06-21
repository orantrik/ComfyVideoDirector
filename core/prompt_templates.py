"""Instruction text and per-segment prompt templates."""

from .schemas import DEFAULT_GLOBAL_STYLE, DEFAULT_GLOBAL_NEGATIVE


def build_continuous_shot_frame_request(total_segments=30):
    return (
        "Because this is one continuous shot split into "
        f"{total_segments} invisible 8-second segments, upload high-resolution "
        "Unreal Engine screenshots as camera-path keyframes.\n\n"
        "Required minimum:\n"
        "1. Start frame\n"
        "2. Exterior approach\n"
        "3. Entrance threshold\n"
        "4. Lobby reveal\n"
        "5. Interior transition\n"
        "6. Main amenity reveal\n"
        "7. Apartment / view reveal\n"
        "8. Final frame\n\n"
        "Recommended:\n"
        "Upload 12-20 frames showing every major turn, doorway, room change, "
        "reveal, lighting change, vertical move, and final composition.\n\n"
        "Order frames in the GUI (drag-and-drop) or prefix filenames like "
        "001_start_exterior_wide.png, 002_approach_tower.png, ..."
    )


SEGMENT_PROMPT_TEMPLATE = """Create an 8-second cinematic architectural video segment that feels like part of one uninterrupted {total_duration} camera move.

Time range: {time_start}-{time_end}
Segment: {segment_id}/{total_segments}
Location: {location}
Start reference frame: {start_reference_frame_id}
End reference frame: {end_reference_frame_id}

Camera movement:
{movement}

Visual action:
{visual_action}

Continuity requirements:
- First frame must match the previous segment's ending composition.
- Last frame must prepare for the next segment's starting composition.
- Maintain the same camera height, speed, lens feel, lighting direction, material palette, and architectural identity.
- Do not introduce a visible cut.
- Do not change the building design.
- Do not teleport to a new space.

Style:
{global_style_prompt}

Negative prompt:
{global_negative_prompt}"""


# Cinematic VISUAL/AUDIO prompt (LTX-2 style). NO meta-instructions, NO
# embedded negative prompt -- only flowing scene description, like a hand-written
# film prompt. Continuity is expressed naturally, not as a rule list.
ONESHOT_TEMPLATE = """VISUAL:
{opening} a cinematic, photorealistic continuous tracking shot, one smooth unbroken take. {presenter}The camera moves {motion}, carrying the viewer from {start_desc} to {end_desc}. The modern luxury architecture is revealed in crisp photorealistic detail with warm natural light, lush landscaping and dramatic shadows; shallow depth of field, subtle film grain and gentle motion blur give it a high-end cinematic feel. {style}

AUDIO:
{audio}"""

DEFAULT_AUDIO = ("Natural high-fidelity ambient sound of the location: a soft "
                 "breeze through landscaped trees, a faint premium-neighborhood "
                 "hum, and gentle footsteps on clean paving.")


def compile_oneshot_prompt(segment, style, presenter, audio):
    sid = segment.get("segment_id", 1)
    opening = ("The opening shot of the tour:" if sid == 1
               else "Continuing the same unbroken take, picking up exactly where the previous shot ended,")
    pres = (presenter.strip() + " ") if presenter and presenter.strip() else ""
    return ONESHOT_TEMPLATE.format(
        opening=opening,
        presenter=pres,
        motion=segment.get("movement", "smoothly and steadily forward"),
        start_desc=segment.get("start_desc", "") or "the current view",
        end_desc=segment.get("end_desc", "") or "the next space",
        style=style,
        audio=(audio.strip() if audio and audio.strip() else DEFAULT_AUDIO),
    )


def compile_segment_prompt(segment, project, global_style_prompt="",
                           global_negative_prompt="", detail_level="standard",
                           total_clips=0, global_presenter="", global_audio=""):
    style = global_style_prompt.strip() or DEFAULT_GLOBAL_STYLE
    negative = global_negative_prompt.strip() or DEFAULT_GLOBAL_NEGATIVE

    if project.get("shot_mode", "fixed_grid") == "keyframe_pairs":
        prompt = compile_oneshot_prompt(segment, style, global_presenter, global_audio)
        return prompt, negative

    total_seconds = project.get("total_duration_seconds", 240)
    total_min = total_seconds // 60
    total_duration = f"{total_min}-minute" if total_seconds % 60 == 0 else f"{total_seconds}-second"

    prompt = SEGMENT_PROMPT_TEMPLATE.format(
        total_duration=total_duration,
        time_start=segment.get("time_start", ""),
        time_end=segment.get("time_end", ""),
        segment_id=segment.get("segment_id", 0),
        total_segments=project.get("total_segments", 30),
        location=segment.get("location", "") or "continuation of the current space",
        start_reference_frame_id=segment.get("start_reference_frame_id", ""),
        end_reference_frame_id=segment.get("end_reference_frame_id", ""),
        movement=segment.get("movement", "smooth forward dolly at a slow walking pace"),
        visual_action=segment.get("visual_action", "continue the camera journey forward"),
        global_style_prompt=style,
        global_negative_prompt=negative,
    )

    if detail_level == "compact":
        # Trim the explanatory continuity block for terse video models.
        prompt = "\n".join(
            line for line in prompt.splitlines()
            if not line.startswith("- ")
        )
    return prompt, negative
