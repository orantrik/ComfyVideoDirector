"""Bind the rendered per-segment clips into one continuous commercial via ffmpeg."""

import os
import glob
import shutil
import tempfile
import subprocess

import folder_paths

from ..core.schemas import IMAGE_EXTENSIONS  # noqa: F401 (kept for parity)

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".avi")


def _find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _collect_clips(clips_folder, pattern):
    if pattern.strip():
        files = sorted(glob.glob(os.path.join(clips_folder, pattern.strip())))
    else:
        files = [
            os.path.join(clips_folder, n)
            for n in sorted(os.listdir(clips_folder))
            if n.lower().endswith(VIDEO_EXTENSIONS)
        ]
    return files


class VideoAssembler:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clips_folder": ("STRING", {"default": ""}),
                "assemble_mode": (["hard_cut_copy", "hard_cut_reencode", "crossfade"],
                                  {"default": "hard_cut_reencode"}),
                "output_filename": ("STRING", {"default": "final_commercial.mp4"}),
                "fps": ("INT", {"default": 24, "min": 1, "max": 120}),
                "crossfade_seconds": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 3.0, "step": 0.1}),
                "clip_duration_seconds": ("INT", {"default": 8, "min": 1, "max": 60}),
            },
            "optional": {
                "continuous_segments": ("CONTINUOUS_SEGMENTS",),
                "clip_glob": ("STRING", {"default": ""}),
                "output_folder": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("final_video_path", "assembly_log")
    FUNCTION = "assemble"
    CATEGORY = "AI Director/Export"
    OUTPUT_NODE = True

    def assemble(self, clips_folder, assemble_mode, output_filename, fps,
                 crossfade_seconds, clip_duration_seconds,
                 continuous_segments=None, clip_glob="", output_folder=""):
        log = []

        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            return ("", "ERROR: ffmpeg not found. Install ffmpeg or `pip install imageio-ffmpeg`.")

        if not clips_folder or not os.path.isdir(clips_folder):
            return ("", f"ERROR: clips_folder is not a valid directory: {clips_folder!r}")

        clips = _collect_clips(clips_folder, clip_glob)
        if not clips:
            return ("", f"ERROR: no video clips found in {clips_folder}")

        expected = len(continuous_segments) if continuous_segments else None
        if expected is not None and expected != len(clips):
            log.append(f"WARNING: {len(clips)} clips found but {expected} segments planned. "
                       "Assembling in sorted filename order anyway.")
        log.append(f"Found {len(clips)} clips. Mode: {assemble_mode}.")
        for i, c in enumerate(clips, 1):
            log.append(f"  {i:02d}. {os.path.basename(c)}")

        out_dir = output_folder.strip() or os.path.join(
            folder_paths.get_output_directory(), "ai_director")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, output_filename.strip() or "final_commercial.mp4")

        try:
            if assemble_mode == "crossfade" and len(clips) > 1 and crossfade_seconds > 0:
                cmd = self._build_crossfade_cmd(
                    ffmpeg, clips, out_path, fps, crossfade_seconds, clip_duration_seconds)
            elif assemble_mode == "hard_cut_copy":
                cmd, listfile = self._build_concat_cmd(ffmpeg, clips, out_path, reencode=False, fps=fps)
            else:  # hard_cut_reencode (default/safe)
                cmd, listfile = self._build_concat_cmd(ffmpeg, clips, out_path, reencode=True, fps=fps)

            log.append("Running ffmpeg...")
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                tail = (proc.stderr or "")[-1500:]
                return ("", "ffmpeg FAILED:\n" + tail + "\n\nLog:\n" + "\n".join(log))
        except Exception as e:
            return ("", f"ERROR during assembly: {e}\n\nLog:\n" + "\n".join(log))

        size_mb = os.path.getsize(out_path) / (1024 * 1024) if os.path.exists(out_path) else 0
        log.append(f"DONE: {out_path} ({size_mb:.1f} MB)")
        return (out_path, "\n".join(log))

    # ------------------------------------------------------------------ #
    def _build_concat_cmd(self, ffmpeg, clips, out_path, reencode, fps):
        # concat demuxer with a temp list file
        fd, listfile = tempfile.mkstemp(suffix=".txt", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for c in clips:
                safe = c.replace("'", "'\\''")
                fh.write(f"file '{safe}'\n")
        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", listfile]
        if reencode:
            cmd += ["-r", str(fps), "-c:v", "libx264", "-crf", "18",
                    "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac"]
        else:
            cmd += ["-c", "copy"]
        cmd.append(out_path)
        return cmd, listfile

    def _build_crossfade_cmd(self, ffmpeg, clips, out_path, fps, t, dur):
        # Chain xfade across all clips; assumes uniform clip duration `dur`.
        cmd = [ffmpeg, "-y"]
        for c in clips:
            cmd += ["-i", c]
        filt = []
        prev = "[0:v]"
        offset = dur - t
        for i in range(1, len(clips)):
            out = f"[v{i}]"
            filt.append(
                f"{prev}[{i}:v]xfade=transition=fade:duration={t}:offset={offset:.3f}{out}"
            )
            prev = out
            offset += dur - t
        filter_complex = ";".join(filt)
        cmd += ["-filter_complex", filter_complex, "-map", prev,
                "-r", str(fps), "-c:v", "libx264", "-crf", "18",
                "-preset", "medium", "-pix_fmt", "yuv420p", out_path]
        return cmd
