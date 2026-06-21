from ..core.prompt_templates import build_continuous_shot_frame_request


class AIDirectorProjectSetup:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_name": ("STRING", {"default": "My Scene"}),
                "content_mode": (["agnostic", "architectural_tour"], {"default": "agnostic"}),
                "shot_mode": (["keyframe_pairs", "fixed_grid"], {"default": "keyframe_pairs"}),
                "segment_duration_seconds": ("INT", {"default": 8, "min": 1, "max": 60}),
                "target_style": ("STRING", {"default": "cinematic, photorealistic", "multiline": True}),
                "continuity_priority": (["low", "medium", "high", "strict"], {"default": "high"}),
            },
            "optional": {
                # Only used by fixed_grid mode. keyframe_pairs derives length from frames.
                "total_duration_seconds": ("INT", {"default": 240, "min": 8, "max": 3600}),
            },
        }

    RETURN_TYPES = ("DIRECTOR_PROJECT", "STRING")
    RETURN_NAMES = ("project", "instruction_text")
    FUNCTION = "create_project"
    CATEGORY = "AI Director/Setup"

    def create_project(self, project_name, content_mode, shot_mode,
                       segment_duration_seconds, target_style, continuity_priority,
                       total_duration_seconds=240):
        if segment_duration_seconds <= 0:
            raise ValueError("segment_duration_seconds must be positive")

        if shot_mode == "fixed_grid":
            if total_duration_seconds % segment_duration_seconds != 0:
                raise ValueError(
                    f"total_duration_seconds ({total_duration_seconds}) must be "
                    f"divisible by segment_duration_seconds ({segment_duration_seconds})."
                )
            total_segments = total_duration_seconds // segment_duration_seconds
        else:
            # keyframe_pairs: segment count is derived from the frame count at
            # plan time (N-1). Unknown here until frames arrive.
            total_segments = 0

        project = {
            "project_name": project_name,
            "mode": "continuous_shot",
            "content_mode": content_mode,
            "shot_mode": shot_mode,
            "total_duration_seconds": total_duration_seconds,
            "segment_duration_seconds": segment_duration_seconds,
            "total_segments": total_segments,
            "target_style": target_style,
            "output_format": "connected video clips, end frame of each = start frame of next",
            "continuity_priority": continuity_priority,
        }

        if shot_mode == "keyframe_pairs":
            instruction_text = (
                "Agnostic one-shot mode. Upload any number of ordered keyframes "
                "(any subject). The Director will create ONE 8-second clip per "
                "consecutive pair: N frames -> N-1 clips. Each clip begins on "
                "frame[i] and ends on frame[i+1], and that end frame is the start "
                "of the next clip, giving a seamless continuous shot. Wire a "
                "Qwen3-VL caption per frame into 'frame_descriptions' so the "
                "Director understands each image and writes the motion between them."
            )
        else:
            instruction_text = build_continuous_shot_frame_request(total_segments)

        return (project, instruction_text)
