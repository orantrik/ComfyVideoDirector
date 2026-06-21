import os
import json
import folder_paths


class ManifestExporter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": ("DIRECTOR_PROJECT",),
                "classified_path_frames": ("CLASSIFIED_PATH_FRAMES",),
                "missing_frame_report": ("MISSING_FRAME_REPORT",),
                "continuous_segments": ("CONTINUOUS_SEGMENTS",),
                "prompt_manifest": ("PROMPT_MANIFEST",),
                "file_prefix": ("STRING", {"default": "ai_director_manifest"}),
            },
            "optional": {
                "output_folder": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("manifest_path", "manifest_summary")
    FUNCTION = "export"
    CATEGORY = "AI Director/Export"
    OUTPUT_NODE = True

    def export(self, project, classified_path_frames, missing_frame_report,
               continuous_segments, prompt_manifest, file_prefix, output_folder=""):
        out_dir = output_folder.strip() or os.path.join(
            folder_paths.get_output_directory(), "ai_director"
        )
        os.makedirs(out_dir, exist_ok=True)

        director_manifest = {
            "project": project,
            "frames": classified_path_frames,
            "missing_report": missing_frame_report,
            "segments": continuous_segments,
            "global_continuity_rules": [
                "One continuous shot; segments are invisible technical cuts.",
                "Maintain camera height, speed, lens feel, lighting, and architecture across seams.",
                "No jump cuts, no teleportation, no building-design changes.",
            ],
            "global_negative_prompt": (prompt_manifest[0]["negative_prompt"]
                                       if prompt_manifest else ""),
        }

        manifest_path = os.path.join(out_dir, f"{file_prefix}.json")
        segments_path = os.path.join(out_dir, "ai_director_segments.json")
        prompts_path = os.path.join(out_dir, "ai_director_prompts.txt")
        report_path = os.path.join(out_dir, "ai_director_missing_report.txt")

        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(director_manifest, fh, indent=2, ensure_ascii=False)
        with open(segments_path, "w", encoding="utf-8") as fh:
            json.dump(continuous_segments, fh, indent=2, ensure_ascii=False)
        with open(prompts_path, "w", encoding="utf-8") as fh:
            for item in (prompt_manifest or []):
                fh.write(f"# Segment {item['segment_id']} [{item['time_range']}] "
                         f"({item['readiness_status']})\n{item['prompt']}\n\n")
        with open(report_path, "w", encoding="utf-8") as fh:
            r = missing_frame_report or {}
            fh.write(f"Continuous shot ready: {r.get('continuous_shot_ready')}\n")
            fh.write(f"Uploaded keyframes: {r.get('uploaded_keyframes')}\n")
            fh.write("Missing required categories: "
                     + ", ".join(r.get("missing_required_categories", [])) + "\n")
            for rec in r.get("recommendations", []):
                fh.write(f"- {rec}\n")

        summary = (
            f"Exported manifest to: {out_dir}\n"
            f"- {os.path.basename(manifest_path)}\n"
            f"- {os.path.basename(segments_path)}\n"
            f"- {os.path.basename(prompts_path)}\n"
            f"- {os.path.basename(report_path)}\n"
            f"Segments: {len(continuous_segments or [])}  "
            f"Prompts: {len(prompt_manifest or [])}"
        )
        return (manifest_path, summary)
