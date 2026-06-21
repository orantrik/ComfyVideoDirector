"""Minimal ComfyUI HTTP client for the orchestrator (stdlib only).

Drives any API-format recipe graph: patch node inputs -> queue -> wait ->
download the produced images. Mirrors the proven helpers in ai_director_run.py.
"""

import os
import json
import time
import uuid
import urllib.request
import urllib.error


def _post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
        except Exception:
            detail = body
        raise RuntimeError(
            f"ComfyUI /prompt returned HTTP {e.code}.\n"
            f"Detail: {json.dumps(detail, indent=2) if isinstance(detail, dict) else detail}"
        ) from None


def _get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def upload_image(comfy_url, filepath, overwrite=True):
    boundary = "----aidir" + uuid.uuid4().hex
    fname = os.path.basename(filepath)
    with open(filepath, "rb") as fh:
        filedata = fh.read()
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="image"; filename="{fname}"\r\n'.encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n" + filedata + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
    body += (b"true" if overwrite else b"false") + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(comfy_url.rstrip("/") + "/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        info = json.loads(r.read().decode("utf-8"))
    sub = info.get("subfolder", "")
    name = info.get("name", fname)
    return f"{sub}/{name}" if sub else name


def queue(comfy_url, api_prompt, client_id):
    out = _post_json(comfy_url.rstrip("/") + "/prompt",
                     {"prompt": api_prompt, "client_id": client_id})
    return out["prompt_id"]


def wait(comfy_url, prompt_id, timeout=1800, poll=2.0):
    t0 = time.time()
    hist = comfy_url.rstrip("/") + f"/history/{prompt_id}"
    while time.time() - t0 < timeout:
        try:
            h = _get_json(hist)
        except urllib.error.URLError:
            h = {}
        if prompt_id in h:
            st = h[prompt_id].get("status", {})
            if st.get("completed", True):
                return h[prompt_id]
        time.sleep(poll)
    raise TimeoutError(f"recipe timed out after {timeout}s")


def _download(comfy_url, info, dest_path):
    q = (f"/view?filename={urllib.parse.quote(info['filename'])}"
         f"&type={info.get('type','output')}"
         f"&subfolder={urllib.parse.quote(info.get('subfolder',''))}")
    with urllib.request.urlopen(comfy_url.rstrip("/") + q, timeout=120) as r:
        data = r.read()
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as fh:
        fh.write(data)
    return dest_path


import urllib.parse  # noqa: E402  (after functions, used by _download)


def upload_audio(comfy_url, filepath, overwrite=True):
    """POST /upload/audio as multipart; returns the stored name."""
    boundary = "----aidir" + uuid.uuid4().hex
    fname = os.path.basename(filepath)
    with open(filepath, "rb") as fh:
        filedata = fh.read()
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="audio"; filename="{fname}"\r\n'.encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n" + filedata + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
    body += (b"true" if overwrite else b"false") + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(comfy_url.rstrip("/") + "/upload/audio", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        info = json.loads(r.read().decode("utf-8"))
    sub = info.get("subfolder", "")
    name = info.get("name", fname)
    return f"{sub}/{name}" if sub else name


def collect_audio(history):
    """Collect audio output items from a completed job history."""
    out = []
    for _node, data in (history.get("outputs", {}) or {}).items():
        for item in data.get("audio", []) or []:
            out.append(item)
    return out


def collect_videos(history, video_node=None):
    """Collect video output items (SaveVideo / VHS_VideoCombine) from job history.

    If video_node is given (e.g. '372'), only that node's outputs are returned.
    Otherwise all nodes' video outputs are returned (first = earliest node).
    """
    out = []
    for node_id, data in (history.get("outputs", {}) or {}).items():
        if video_node and str(node_id) != str(video_node):
            continue
        for key in ("gifs", "videos"):
            for item in data.get(key, []) or []:
                out.append(item)
    return out


def run_recipe_video(comfy_url, recipe_api_json_path, patches, out_path, client_id=None,
                     timeout=3600, video_node=None):
    """Run an LTX / video recipe. Saves the video output to out_path (.mp4).

    video_node: if set, only collect output from that specific node ID (e.g. '372').
    When multiple SaveVideo nodes exist (draft + final), use this to target the final one.
    Unknown patch node IDs are silently skipped (warning only) to allow partial patching.
    """
    client_id = client_id or uuid.uuid4().hex
    api = _load_recipe(recipe_api_json_path, patches, warn_missing=True)
    pid = queue(comfy_url, api, client_id)
    hist = wait(comfy_url, pid, timeout=timeout)
    items = collect_videos(hist, video_node=video_node)
    if not items and video_node:
        # Fallback: collect from any node if the specified one had no output
        items = collect_videos(hist)
    if not items:
        raise RuntimeError(f"video recipe produced no output (prompt {pid})")
    return _download(comfy_url, items[0], out_path)


def _load_recipe(path, patches, warn_missing=False):
    """Load an API-format recipe JSON, strip comment keys (starting with '_'),
    apply patches, and return the ready-to-queue dict."""
    api = json.load(open(path, encoding="utf-8"))
    # Strip documentation/comment keys so ComfyUI doesn't trip on them
    api = {k: v for k, v in api.items() if not k.startswith("_")}
    for nid, inputs in (patches or {}).items():
        if str(nid) not in api:
            msg = (f"  [warn] patch targets unknown node {nid} in "
                   f"{os.path.basename(path)} — skipping")
            if warn_missing:
                print(msg)
            else:
                raise KeyError(f"recipe {os.path.basename(path)} has no node id {nid}")
            continue
        api[str(nid)].setdefault("inputs", {}).update(inputs)
    return api


def collect_images(history):
    out = []
    for _node, data in (history.get("outputs", {}) or {}).items():
        for key in ("images", "gifs"):
            for im in data.get(key, []) or []:
                out.append(im)
    return out


def run_recipe(comfy_url, recipe_api_json_path, patches, out_path, client_id=None,
               timeout=1800):
    """Load an API-format recipe, apply patches, queue, save the FIRST output to
    out_path. `patches` = {node_id(str): {input_name: value}}."""
    client_id = client_id or uuid.uuid4().hex
    api = _load_recipe(recipe_api_json_path, patches)
    pid = queue(comfy_url, api, client_id)
    hist = wait(comfy_url, pid, timeout=timeout)
    imgs = collect_images(hist)
    if not imgs:
        raise RuntimeError(f"recipe produced no images (prompt {pid})")
    return _download(comfy_url, imgs[0], out_path)


def run_recipe_audio(comfy_url, recipe_api_json_path, patches, out_path, client_id=None,
                     timeout=600):
    """Run a TTS recipe whose output is AUDIO. Saves first audio output to out_path."""
    client_id = client_id or uuid.uuid4().hex
    api = _load_recipe(recipe_api_json_path, patches)
    pid = queue(comfy_url, api, client_id)
    hist = wait(comfy_url, pid, timeout=timeout)
    items = collect_audio(hist)
    if not items:
        raise RuntimeError(f"TTS recipe produced no audio (prompt {pid})")
    return _download(comfy_url, items[0], out_path)


def run_recipe_text(comfy_url, recipe_api_json_path, patches, client_id=None,
                    timeout=600, text_node=None):
    """Run a recipe whose output is TEXT (e.g. Qwen3-VL). Returns the string from
    the node's history outputs."""
    client_id = client_id or uuid.uuid4().hex
    api = _load_recipe(recipe_api_json_path, patches, warn_missing=True)
    pid = queue(comfy_url, api, client_id)
    hist = wait(comfy_url, pid, timeout=timeout)
    outs = hist.get("outputs", {}) or {}
    # Prefer a named node; else scan for any string-like output.
    nodes = [str(text_node)] if text_node else list(outs.keys())
    for nid in nodes:
        data = outs.get(nid, {})
        for key, val in data.items():
            if isinstance(val, list) and val and isinstance(val[0], str):
                return "\n".join(val)
            if isinstance(val, str):
                return val
    return ""
