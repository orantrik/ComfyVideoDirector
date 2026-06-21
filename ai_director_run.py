#!/usr/bin/env python3
"""
AI Director - fully automated batch runner.
===========================================

Drives ComfyUI through its HTTP API to render a whole continuous scene from a
folder of ordered keyframes, feeding each clip's generated LAST frame forward as
the START frame of the next clip, then assembles everything into one video.

It does NOT run inside ComfyUI; run it from a terminal while ComfyUI is up:

    python ai_director_run.py \
        --frames "C:/frames" \
        --workflow-api "C:/.../ClaudeVideoGen_api.json" \
        --output-dir "C:/.../ComfyUI/output" \
        --captions "C:/frames/captions.txt"

Get the workflow-api file from ComfyUI: open your graph, then
"Workflow -> Export (API)" (a.k.a. Save (API Format)). Node ids are preserved.

Feed-forward: clip 1 starts on keyframe 1; clip i (>1) starts on the previous
clip's extracted last frame. The per-clip prompt already describes the next
keyframe as the target composition.
"""

import os
import sys
import time
import json
import glob
import shutil
import argparse
import tempfile
import subprocess

# --------------------------------------------------------------------------- #
#  Node id map for ClaudeVideoGen.json (override via CLI if yours differ)
# --------------------------------------------------------------------------- #
DEFAULTS = {
    "positive_node": "304",      # CLIPTextEncode (positive) .inputs.text
    "negative_node": "315",      # CLIPTextEncode (negative) .inputs.text
    "start_image_node": "349",   # LoadImage .inputs.image  (name in input dir)
    "save_node": "372",          # SaveVideo .inputs.filename_prefix
    "end_image_node": "",        # OPTIONAL: LoadImage for the end keyframe (set
                                 # after you add LTXVAddGuide). Empty = start-only.
}

PKG_DIR = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
#  Minimal HTTP (stdlib only)
# --------------------------------------------------------------------------- #
import urllib.request
import urllib.error
import uuid


def _http_json(url, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def upload_image(comfy_url, filepath):
    """POST /upload/image as multipart; returns the stored input name."""
    boundary = "----aidir" + uuid.uuid4().hex
    fname = os.path.basename(filepath)
    with open(filepath, "rb") as fh:
        filedata = fh.read()
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="image"; filename="{fname}"\r\n'.encode()
    body += b"Content-Type: image/png\r\n\r\n"
    body += filedata + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        comfy_url.rstrip("/") + "/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        info = json.loads(r.read().decode("utf-8"))
    name = info.get("name", fname)
    sub = info.get("subfolder", "")
    return f"{sub}/{name}" if sub else name


def queue_prompt(comfy_url, api_prompt, client_id):
    out = _http_json(comfy_url.rstrip("/") + "/prompt",
                     {"prompt": api_prompt, "client_id": client_id})
    return out["prompt_id"]


def wait_done(comfy_url, prompt_id, timeout=1800, poll=2.0):
    t0 = time.time()
    hist_url = comfy_url.rstrip("/") + f"/history/{prompt_id}"
    while time.time() - t0 < timeout:
        try:
            h = _http_get(hist_url)
        except urllib.error.URLError:
            h = {}
        if prompt_id in h:
            status = h[prompt_id].get("status", {})
            if status.get("completed", True):
                return h[prompt_id]
        time.sleep(poll)
    raise TimeoutError(f"clip timed out after {timeout}s")


# --------------------------------------------------------------------------- #
#  Director manifest (reuses the tested package logic)
# --------------------------------------------------------------------------- #
def load_director(pkg_dir):
    # The nodes import folder_paths (a ComfyUI module); stub it for standalone.
    if "folder_paths" not in sys.modules:
        import types
        fp = types.ModuleType("folder_paths")
        tmp = tempfile.mkdtemp()
        fp.get_temp_directory = lambda: tmp
        fp.get_output_directory = lambda: tmp
        sys.modules["folder_paths"] = fp
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aidir", os.path.join(pkg_dir, "__init__.py"),
        submodule_search_locations=[pkg_dir])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aidir"] = mod
    spec.loader.exec_module(mod)
    return mod.NODE_CLASS_MAPPINGS


def build_manifest(M, frames_folder, captions, motion, style, neg, seg_dur,
                   presenter="", audio=""):
    proj, _ = M["AIDirectorProjectSetup"]().create_project(
        "AutoScene", "agnostic", "keyframe_pairs", seg_dur, style, "high")
    frames, isum = M["UnrealFrameIntake"]().intake(
        proj, "auto_guess", selected_frames_folder=frames_folder,
        frame_descriptions=captions)
    cf, _ = M["FrameClassifier"]().classify(
        frames, "filename_rules", "eye-level", "slow walking pace", "wide lens")
    rep, rtext, ready = M["ContinuousPathValidator"]().validate(proj, cf, "normal")
    segs, ssum = M["SegmentPlanner"]().plan(proj, cf, rep, True, motion_notes=motion)
    manifest, _ = M["PromptCompiler"]().compile(
        proj, segs, style, neg, "standard",
        global_presenter=presenter, global_audio=audio)
    return cf, manifest, ssum


# --------------------------------------------------------------------------- #
#  ffmpeg helpers
# --------------------------------------------------------------------------- #
def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def extract_last_frame(ffmpeg, video, out_png):
    # Grab the final frame (seek to just before EOF).
    cmd = [ffmpeg, "-y", "-sseof", "-0.25", "-i", video,
           "-frames:v", "1", "-update", "1", "-q:v", "1", out_png]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(out_png):
        # fallback: reverse + first frame
        cmd = [ffmpeg, "-y", "-i", video, "-vf", "reverse",
               "-frames:v", "1", out_png]
        subprocess.run(cmd, capture_output=True, text=True)
    return os.path.exists(out_png)


def newest_matching(output_dir, prefix):
    # prefix like "tour/shot_01" -> output_dir/tour/shot_01*.{mp4,mov,webm,mkv}
    base = os.path.join(output_dir, prefix.replace("/", os.sep))
    hits = []
    for ext in ("mp4", "mov", "webm", "mkv"):
        hits += glob.glob(base + "*." + ext)
    return max(hits, key=os.path.getmtime) if hits else None


def assemble(ffmpeg, clips, out_path, fps):
    fd, listfile = tempfile.mkstemp(suffix=".txt", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for c in clips:
            fh.write(f"file '{c}'\n".replace("\\", "/"))
    cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
           "-r", str(fps), "-c:v", "libx264", "-crf", "18", "-preset", "medium",
           "-pix_fmt", "yuv420p", out_path]
    subprocess.run(cmd, capture_output=True, text=True)
    return os.path.exists(out_path)


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="AI Director automated batch runner")
    ap.add_argument("--frames", required=True, help="folder of ordered keyframes")
    ap.add_argument("--workflow-api", help="ComfyUI API-format workflow JSON")
    ap.add_argument("--output-dir", help="ComfyUI output directory (to find clips)")
    ap.add_argument("--captions", default="", help="optional file: one caption per frame, in order")
    ap.add_argument("--motion", default="", help="optional file: one camera-move line per transition")
    ap.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    ap.add_argument("--save-base", default="tour/shot")
    ap.add_argument("--seg-seconds", type=int, default=8)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--pkg-dir", default=PKG_DIR)
    ap.add_argument("--style", default="cinematic, photorealistic, continuous camera move")
    ap.add_argument("--negative", default="blurry, distorted, jump cut, flicker, low-res, cartoon")
    ap.add_argument("--presenter", default="", help="recurring on-screen presenter description (file or text)")
    ap.add_argument("--audio", default="", help="recurring narration/audio direction (file or text)")
    ap.add_argument("--final-name", default="final_commercial.mp4")
    for k, v in DEFAULTS.items():
        ap.add_argument("--" + k.replace("_", "-"), default=v)
    ap.add_argument("--dry-run", action="store_true", help="plan only; no API/ffmpeg")
    args = ap.parse_args()

    def _maybe_file(v):
        return open(v, encoding="utf-8").read() if v and os.path.isfile(v) else (v or "")
    captions = _maybe_file(args.captions)
    motion = _maybe_file(args.motion)
    presenter = _maybe_file(args.presenter)
    audio = _maybe_file(args.audio)

    M = load_director(args.pkg_dir)
    cf, manifest, ssum = build_manifest(
        M, args.frames, captions, motion, args.style, args.negative, args.seg_seconds,
        presenter=presenter, audio=audio)
    frames_sorted = sorted(cf, key=lambda f: f.get("order", 9999))
    path_by_id = {f["frame_id"]: f.get("image_path", "") for f in cf}

    print("=" * 64)
    print(ssum)
    print(f"Clips to render: {len(manifest)}")
    print("=" * 64)
    for item in manifest:
        print(f"  clip {item['segment_id']:>2}: {item['start_reference_frame_id']} -> "
              f"{item['end_reference_frame_id']}   save={args.save_base}_{item['segment_id']:02d}")

    if args.dry_run:
        print("\n[dry-run] sample prompt (clip 1):\n")
        print(manifest[0]["prompt"][:600])
        return

    # Live run requires server bits.
    if not args.workflow_api or not os.path.isfile(args.workflow_api):
        sys.exit("ERROR: --workflow-api (API-format export) is required for a live run.")
    if not args.output_dir or not os.path.isdir(args.output_dir):
        sys.exit("ERROR: --output-dir must point at ComfyUI's output folder.")
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        sys.exit("ERROR: ffmpeg not found (pip install imageio-ffmpeg).")

    api_template = json.load(open(args.workflow_api, encoding="utf-8"))
    client_id = uuid.uuid4().hex
    work = tempfile.mkdtemp(prefix="aidir_")
    rendered = []
    prev_last_frame = None  # absolute path to feed forward

    for i, item in enumerate(manifest, start=1):
        # Start frame: clip 1 = first keyframe; else previous generated last frame.
        if prev_last_frame and os.path.exists(prev_last_frame):
            start_img = prev_last_frame
        else:
            start_img = frames_sorted[0].get("image_path", "")
        print(f"\n--- Clip {i}/{len(manifest)}  start={os.path.basename(start_img)} ---")

        uploaded = upload_image(args.comfy_url, start_img)

        api = json.loads(json.dumps(api_template))  # deep copy
        save_prefix = f"{args.save_base}_{item['segment_id']:02d}"
        api[args.positive_node]["inputs"]["text"] = item["prompt"]
        api[args.negative_node]["inputs"]["text"] = item["negative_prompt"]
        api[args.start_image_node]["inputs"]["image"] = uploaded
        api[args.save_node]["inputs"]["filename_prefix"] = save_prefix

        # End-keyframe anchoring (only if you added LTXVAddGuide + an end LoadImage).
        if args.end_image_node:
            end_path = path_by_id.get(item.get("end_reference_frame_id", ""), "")
            if end_path and os.path.exists(end_path):
                end_uploaded = upload_image(args.comfy_url, end_path)
                api[args.end_image_node]["inputs"]["image"] = end_uploaded
                print(f"   end anchor: {os.path.basename(end_path)}")

        pid = queue_prompt(args.comfy_url, api, client_id)
        print(f"   queued {pid}; waiting...")
        wait_done(args.comfy_url, pid)

        clip = newest_matching(args.output_dir, save_prefix)
        if not clip:
            print(f"   WARNING: no output found for {save_prefix}; stopping feed-forward.")
            continue
        print(f"   rendered: {clip}")
        rendered.append(clip)

        nxt = os.path.join(work, f"last_{i:02d}.png")
        if extract_last_frame(ffmpeg, clip, nxt):
            prev_last_frame = nxt
        else:
            print("   WARNING: could not extract last frame; next clip uses keyframe.")
            prev_last_frame = None

    if rendered:
        final = os.path.join(args.output_dir, args.final_name)
        if assemble(ffmpeg, rendered, final, args.fps):
            print(f"\nDONE. Final scene: {final}  ({len(rendered)} clips)")
        else:
            print("\nClips rendered but assembly failed; clips are in the output folder.")
    else:
        print("\nNo clips rendered.")


if __name__ == "__main__":
    main()
