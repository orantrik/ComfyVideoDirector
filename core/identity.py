"""Identity container: the durable per-project source of truth.

Folder + JSON layout that every pipeline stage reads from and writes to, so
NanoBanana/LTX generations always draw from locked identities (descriptions +
reference packshots/sheets + coordinates) instead of hallucinating.

Pure stdlib so it is fully testable offline.
"""

import os
import json
import datetime


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


# --------------------------------------------------------------------------- #
#  Paths
# --------------------------------------------------------------------------- #
def scene_id(n):
    return f"scene_{int(n):02d}"


def scene_dir(root, n):
    return os.path.join(root, "scenes", scene_id(n))


def cast_dir(root):
    # Cast (spokesman + actors) is shared across scenes.
    return os.path.join(root, "cast")


SCENE_SUBDIRS = [
    "identity/furniture", "identity/objects",
    "prompts", "audio", "renders",
]
CAST_SUBDIRS = ["spokesman/clothes_packshots", "actors"]


def init_project(root):
    os.makedirs(os.path.join(root, "scenes"), exist_ok=True)
    for sub in CAST_SUBDIRS:
        os.makedirs(os.path.join(cast_dir(root), sub), exist_ok=True)
    idx_path = os.path.join(root, "identity_index.json")
    if not os.path.exists(idx_path):
        save_index(root, {
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "scenes": {}, "cast": {"spokesman": {}, "actors": {}},
        })
    return root


def init_scene(root, n):
    d = scene_dir(root, n)
    for sub in SCENE_SUBDIRS:
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
#  Small IO helpers
# --------------------------------------------------------------------------- #
def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text or "")
    return path


def read_text(path, default=""):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
    return path


def read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
#  Master index
# --------------------------------------------------------------------------- #
def index_path(root):
    return os.path.join(root, "identity_index.json")


def load_index(root):
    return read_json(index_path(root), {"scenes": {}, "cast": {"spokesman": {}, "actors": {}}})


def save_index(root, idx):
    return write_json(index_path(root), idx)


def register_scene_item(root, n, kind, item_id, record):
    """kind in {'furniture','objects'}; record holds desc_path, packshot_path, etc."""
    idx = load_index(root)
    sk = scene_id(n)
    idx.setdefault("scenes", {}).setdefault(sk, {}).setdefault(kind, {})[item_id] = record
    save_index(root, idx)
    return record


def register_cast(root, role, item_id, record):
    """role in {'spokesman','actors'}."""
    idx = load_index(root)
    cast = idx.setdefault("cast", {})
    if role == "spokesman":
        cast["spokesman"] = record
    else:
        cast.setdefault("actors", {})[item_id] = record
    save_index(root, idx)
    return record


# --------------------------------------------------------------------------- #
#  Item path helpers
# --------------------------------------------------------------------------- #
def register_scene_space(root, n, record):
    """Register scene-level space data (space_map, stabilized image paths) in the index."""
    idx = load_index(root)
    idx.setdefault("scenes", {}).setdefault(scene_id(n), {})["space"] = record
    save_index(root, idx)
    return record


def furniture_item_dir(root, n, item_id):
    return os.path.join(scene_dir(root, n), "identity", "furniture", item_id)


def object_item_dir(root, n, item_id):
    return os.path.join(scene_dir(root, n), "identity", "objects", item_id)


def reference_images(root, n=None):
    """Collect all locked reference images (packshots, sheets, portraits) to pass
    back into generations. If n given, scene refs + cast; else cast only."""
    refs = []
    cd = cast_dir(root)
    for dirpath, _dirs, files in os.walk(cd):
        for f in files:
            if f.lower().endswith(IMAGE_EXTS):
                refs.append(os.path.join(dirpath, f))
    if n is not None:
        sid = os.path.join(scene_dir(root, n), "identity")
        for dirpath, _dirs, files in os.walk(sid):
            for f in files:
                if f.lower().endswith(IMAGE_EXTS):
                    refs.append(os.path.join(dirpath, f))
    return sorted(refs)
