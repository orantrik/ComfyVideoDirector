from ..core.prompt_templates import compile_segment_prompt
from ..core.schemas import DEFAULT_GLOBAL_STYLE, DEFAULT_GLOBAL_NEGATIVE


class PromptCompiler:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": ("DIRECTOR_PROJECT",),
                "continuous_segments": ("CONTINUOUS_SEGMENTS",),
                "global_style_prompt": ("STRING", {"default": DEFAULT_GLOBAL_STYLE, "multiline": True}),
                "global_negative_prompt": ("STRING", {"default": DEFAULT_GLOBAL_NEGATIVE, "multiline": True}),
                "prompt_detail_level": (["compact", "standard", "detailed"], {"default": "standard"}),
            },
            "optional": {
                # The recurring on-screen presenter/character, reused in EVERY clip.
                "global_presenter": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": (
                        "The same elegant woman in her early 30s with long wavy "
                        "golden-brown hair, wearing a tailored trench coat, walks "
                        "and gestures as she presents the development, looking back "
                        "toward the camera with warm confidence."
                    ),
                }),
                # The recurring AUDIO / narration direction, reused in every clip.
                "global_audio": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": (
                        "High-fidelity cinematic audio of the woman's warm, "
                        "confident voice narrating in Hebrew, with soft ambient "
                        "breeze and gentle footsteps on stone."
                    ),
                }),
            },
        }

    RETURN_TYPES = ("PROMPT_MANIFEST", "STRING")
    RETURN_NAMES = ("prompt_manifest", "prompts_text")
    FUNCTION = "compile"
    CATEGORY = "AI Director/Prompts"

    def compile(self, project, continuous_segments, global_style_prompt,
                global_negative_prompt, prompt_detail_level,
                global_presenter="", global_audio=""):
        manifest = []
        text_blocks = []
        total_clips = len(continuous_segments or [])

        for seg in continuous_segments or []:
            prompt, negative = compile_segment_prompt(
                seg, project, global_style_prompt, global_negative_prompt,
                prompt_detail_level, total_clips=total_clips,
                global_presenter=global_presenter, global_audio=global_audio
            )
            # Write the compiled prompt back onto the segment too.
            seg["prompt"] = prompt
            seg["negative_prompt"] = negative

            item = {
                "segment_id": seg["segment_id"],
                "time_range": f"{seg['time_start']}-{seg['time_end']}",
                "start_reference_frame_id": seg.get("start_reference_frame_id", ""),
                "end_reference_frame_id": seg.get("end_reference_frame_id", ""),
                "prompt": prompt,
                "negative_prompt": negative,
                "readiness_status": seg["readiness_status"],
                "validation_criteria": seg["validation_criteria"],
            }
            manifest.append(item)

            text_blocks.append(
                f"===== SEGMENT {seg['segment_id']}/{project.get('total_segments',30)} "
                f"[{item['time_range']}]  status={seg['readiness_status']} =====\n"
                f"{prompt}\n"
            )

        prompts_text = "\n".join(text_blocks) if text_blocks else "No segments to compile."
        return (manifest, prompts_text)
