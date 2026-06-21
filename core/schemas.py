"""Data models and constants for the AI Director.

Across ComfyUI sockets we pass plain dicts/lists (JSON-friendly) so the
pipeline stays robust and exportable. The dataclasses below document the
shape of those dicts and provide default factories.
"""

from dataclasses import dataclass, field, asdict


# --------------------------------------------------------------------------- #
#  Category vocabulary
# --------------------------------------------------------------------------- #

PATH_FRAME_CATEGORIES = [
    "start_frame", "exterior_establishing", "exterior_approach", "arrival_plaza",
    "entrance_threshold", "lobby_reveal", "lobby_movement", "interior_transition",
    "corridor_transition", "elevator_transition", "stairs_transition",
    "amenity_reveal", "amenity_movement", "apartment_entry", "apartment_reveal",
    "view_reveal", "balcony_transition", "rooftop_arrival", "rooftop_hero",
    "final_frame", "detail_checkpoint", "lifestyle_checkpoint", "unknown",
]

REQUIRED_CONTINUOUS_PATH_CATEGORIES = [
    "start_frame", "exterior_approach", "entrance_threshold", "lobby_reveal",
    "interior_transition", "amenity_reveal", "view_reveal", "final_frame",
]

RECOMMENDED_CONTINUOUS_PATH_CATEGORIES = [
    "exterior_establishing", "arrival_plaza", "lobby_movement",
    "corridor_transition", "elevator_transition", "apartment_entry",
    "apartment_reveal", "rooftop_arrival", "rooftop_hero",
]

# A required category can be satisfied by any member of its equivalence group.
# e.g. "interior_transition" is covered by a corridor/elevator/stairs frame.
REQUIRED_EQUIVALENTS = {
    "start_frame": ["start_frame"],
    "exterior_approach": ["exterior_approach", "exterior_establishing"],
    "entrance_threshold": ["entrance_threshold"],
    "lobby_reveal": ["lobby_reveal"],
    "interior_transition": ["interior_transition", "corridor_transition",
                            "elevator_transition", "stairs_transition"],
    "amenity_reveal": ["amenity_reveal"],
    "view_reveal": ["view_reveal", "balcony_transition"],
    "final_frame": ["final_frame"],
}

CATEGORY_KEYWORDS = {
    "start_frame": ["start", "opening", "begin"],
    "exterior_establishing": ["establishing", "wide", "skyline"],
    "exterior_approach": ["approach", "push", "tower", "exterior"],
    "arrival_plaza": ["plaza", "arrival", "street"],
    "entrance_threshold": ["entrance", "threshold", "door", "lobby_entry"],
    "lobby_reveal": ["lobby", "reveal"],
    "lobby_movement": ["lobby_move", "lobby_forward", "reception"],
    "interior_transition": ["transition", "interior_transition"],
    "corridor_transition": ["corridor", "hallway"],
    "elevator_transition": ["elevator", "lift"],
    "stairs_transition": ["stairs", "stair"],
    "amenity_reveal": ["amenity", "gym", "pool", "lounge", "spa"],
    "apartment_entry": ["apartment_entry", "unit_entry", "residence_entry"],
    "apartment_reveal": ["apartment", "living", "bedroom", "kitchen", "residence"],
    "view_reveal": ["view", "balcony", "window", "skyline"],
    "rooftop_arrival": ["rooftop_arrival", "roof_arrival"],
    "rooftop_hero": ["rooftop", "roof", "terrace"],
    "final_frame": ["final", "ending", "close", "closing"],
}

# When several keyword groups match, higher in this list wins.
CATEGORY_PRIORITY = [
    "final_frame", "start_frame", "entrance_threshold", "lobby_reveal",
    "amenity_reveal", "apartment_reveal", "view_reveal", "rooftop_hero",
    "exterior_approach", "unknown",
]

READINESS_STATUSES = ["ready", "needs_review", "blocked"]
RISK_LEVELS = ["low", "medium", "high", "blocking"]

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")

DEFAULT_GLOBAL_STYLE = (
    "Cinematic luxury architectural visualization, realistic Unreal Engine look, "
    "premium calm movement, stabilized camera, smooth dolly motion, wide "
    "architectural lens with minimal distortion, natural lighting continuity, "
    "high-end real estate film, physically plausible camera path, elegant pacing."
)

DEFAULT_GLOBAL_NEGATIVE = (
    "No jump cuts, no sudden location change, no change in building design, no "
    "warped architecture, no inconsistent facade, no flickering lights, no "
    "unstable camera, no fisheye distortion, no surreal objects, no cartoon "
    "style, no random people appearing suddenly, no impossible camera "
    "teleportation, no mismatched lighting, no low-resolution artifacts."
)

DEFAULT_SEAM_RULE = (
    "The first frame of this segment must visually continue from the final frame "
    "of the previous segment. Maintain the same camera direction, speed, lens "
    "feel, lighting logic, architecture, and movement momentum. No visible jump "
    "cut. No sudden change in building design. No sudden location jump."
)


# --------------------------------------------------------------------------- #
#  Dataclasses (documentation + default factories)
# --------------------------------------------------------------------------- #

@dataclass
class ProjectConfig:
    project_name: str
    mode: str = "continuous_shot"
    total_duration_seconds: int = 240
    segment_duration_seconds: int = 8
    total_segments: int = 30
    target_style: str = "cinematic architectural visualization"
    output_format: str = "30 connected 8-second video segments"
    continuity_priority: str = "high"


@dataclass
class PathFrame:
    frame_id: str
    order: int
    image_path: str
    file_name: str
    category: str
    location: str = ""
    story_time_target: str = ""
    path_role: str = ""
    visual_description: str = ""
    previous_frame_id: str = ""
    next_frame_id: str = ""
    camera_height: str = "eye-level"
    camera_direction: str = "forward"
    camera_speed: str = "slow walking pace"
    movement_type: str = "dolly forward"
    lens_feel: str = "wide architectural lens, minimal distortion"
    must_match_exactly: bool = True
    continuity_tags: list = field(default_factory=list)
    risk_notes: list = field(default_factory=list)
    missing_notes: list = field(default_factory=list)


@dataclass
class ContinuousSegment:
    segment_id: int
    clip_id: int
    time_start: str
    time_end: str
    duration_seconds: int
    start_reference_frame_id: str
    end_reference_frame_id: str
    location: str
    movement: str
    visual_action: str
    transition_rule: str
    seam_rule_previous: str
    seam_rule_next: str
    continuity_constraints: list
    prompt: str
    negative_prompt: str
    validation_criteria: list
    readiness_status: str
    missing_elements: list
    risk_level: str


def new_path_frame(**kwargs) -> dict:
    """Build a PathFrame dict with all defaults filled in."""
    base = asdict(PathFrame(
        frame_id=kwargs.pop("frame_id", "frame"),
        order=kwargs.pop("order", 0),
        image_path=kwargs.pop("image_path", ""),
        file_name=kwargs.pop("file_name", ""),
        category=kwargs.pop("category", "unknown"),
    ))
    base.update(kwargs)
    return base
