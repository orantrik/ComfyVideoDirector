"""Project gallery: scan all generated elements + regenerate any single one.

Used by the GUI launcher to:
  * scan_project(root)  -> structured list of every previewable element (with
                            its image path, label, and regen metadata).
  * regen_element(root, meta, client) -> regenerate exactly ONE element image,
                            overwriting it in place, using the same prompt the
                            pipeline originally used (read back from the
                            identity container so the identity stays locked).

Pure stdlib + the project's own core modules — no ComfyUI import here; the
caller supplies a client (ComfyClient or DryRunClient) with a .generate() method.
"""

import os
import json

from core import identity as ID
from core import prompts_archviz as P

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


# --------------------------------------------------------------------------- #
#  Scanning
# --------------------------------------------------------------------------- #
def _item(etype, path, label, regen=True, **meta):
    """Build one gallery element record."""
    rec = {
        "etype": etype,
        "path": path,
        "label": label,
        "exists": os.path.isfile(path),
        "regen": regen,
    }
    rec.update(meta)
    return rec


def _space_desc(root, n):
    return ID.read_json(
        os.path.join(ID.scene_dir(root, n), "identity", "space.json"), {}
    ).get("description", "")


def scan_project(root):
    """Return ordered groups: [{'title': str, 'items': [item, ...]}, ...]."""
    groups = []

    # ---- CAST -------------------------------------------------------------
    cast_items = []
    sdir = os.path.join(ID.cast_dir(root), "spokesman")
    sp_desc = ID.read_text(os.path.join(sdir, "desc.txt"), "")
    cast_items.append(_item("cast_sheet", os.path.join(sdir, "sheet_7angle.png"),
                            "Spokesman — 7-angle sheet",
                            role="spokesman", desc=sp_desc, angles=7))
    cast_items.append(_item("cast_portrait", os.path.join(sdir, "portrait.png"),
                            "Spokesman — portrait",
                            role="spokesman", desc=sp_desc))
    cpdir = os.path.join(sdir, "clothes_packshots")
    if os.path.isdir(cpdir):
        for f in sorted(os.listdir(cpdir)):
            if f.lower().endswith(IMAGE_EXTS):
                cast_items.append(_item("garment", os.path.join(cpdir, f),
                                        f"Spokesman garment — {f}", regen=False))

    actors_dir = os.path.join(ID.cast_dir(root), "actors")
    if os.path.isdir(actors_dir):
        for aid in sorted(os.listdir(actors_dir)):
            adir = os.path.join(actors_dir, aid)
            if not os.path.isdir(adir):
                continue
            a_desc = ID.read_text(os.path.join(adir, "desc.txt"), "")
            cast_items.append(_item("cast_sheet", os.path.join(adir, "sheet.png"),
                                    f"Actor {aid} — sheet",
                                    role="actors", item_id=aid, desc=a_desc, angles=5))
            cast_items.append(_item("cast_portrait", os.path.join(adir, "portrait.png"),
                                    f"Actor {aid} — portrait",
                                    role="actors", item_id=aid, desc=a_desc))
    if cast_items:
        groups.append({"title": "Cast", "items": cast_items})

    # ---- SCENES -----------------------------------------------------------
    scenes_dir = os.path.join(root, "scenes")
    if os.path.isdir(scenes_dir):
        for sid in sorted(os.listdir(scenes_dir)):
            if not sid.startswith("scene_"):
                continue
            try:
                n = int(sid.split("_")[1])
            except (IndexError, ValueError):
                continue
            sd = os.path.join(scenes_dir, sid)
            items = []

            items.append(_item("source", os.path.join(sd, "source.png"),
                               "Source screenshot", regen=False, scene=n))

            for kind, dirfn in (("furniture", ID.furniture_item_dir),
                                ("objects", ID.object_item_dir)):
                base = os.path.join(sd, "identity", kind)
                if os.path.isdir(base):
                    for iid in sorted(os.listdir(base)):
                        idir = os.path.join(base, iid)
                        if not os.path.isdir(idir):
                            continue
                        d = ID.read_text(os.path.join(idir, "desc.txt"), "")
                        name = d.split("|")[0].strip() if "|" in d else iid
                        items.append(_item(
                            "packshot",
                            os.path.join(idir, "packshot_4view.png"),
                            f"{kind[:-1]} {iid}: {name}",
                            scene=n, kind=kind, item_id=iid, desc=d))

            items.append(_item("space_map",
                               os.path.join(sd, "identity", "space_map.png"),
                               "Space map", scene=n))
            items.append(_item("stabilized",
                               os.path.join(sd, "renders", "empty_space_stabilized.png"),
                               "Empty space (stabilized)", scene=n))
            for v in range(1, 5):
                items.append(_item("hero",
                                   os.path.join(sd, "renders", f"hero_v{v}.png"),
                                   f"Hero variation {v}", scene=n, variation=v))
            items.append(_item("hero_locked",
                               os.path.join(sd, "renders", "hero_locked.png"),
                               "Hero LOCKED", regen=False, scene=n))

            groups.append({"title": f"Scene {n}", "items": items})

    return groups


# --------------------------------------------------------------------------- #
#  Regeneration (one element at a time)
# --------------------------------------------------------------------------- #
def _prev_master(root, n):
    if n and n > 1:
        return ID.read_text(
            os.path.join(ID.scene_dir(root, n - 1), "prompts", "master_prompt.txt"), "")
    return ""


def regen_element(root, meta, client):
    """Regenerate exactly one element image in place. Returns the output path.

    Raises ValueError if the element type does not support regeneration.
    """
    etype = meta["etype"]
    path = meta["path"]
    n = meta.get("scene")

    if etype == "packshot":
        d = meta.get("desc") or ID.read_text(
            os.path.join(os.path.dirname(path), "desc.txt"), "")
        parts = [p.strip() for p in d.split("|")]
        name = parts[0] if parts else meta.get("item_id", "item")
        desc = parts[1] if len(parts) > 1 else ""
        prompt = P.fill(P.GEN_PACKSHOT_4VIEW, desc=f"{name}, {desc}")
        return client.generate(prompt, [], path)

    if etype == "space_map":
        space = _space_desc(root, n)
        return client.generate(P.fill(P.GEN_SPACE_MAP, desc=space), [], path)

    if etype == "stabilized":
        space = _space_desc(root, n)
        refs = ID.reference_images(root, n)
        return client.generate(P.fill(P.GEN_EMPTY_SPACE_STABILIZED, space=space),
                               refs, path)

    if etype == "hero":
        space = _space_desc(root, n)
        coords = ID.read_json(
            os.path.join(ID.scene_dir(root, n), "coordinates.json"), [])
        prev = _prev_master(root, n)
        prev_ctx = (f" Arriving from: {prev[:300]}.") if prev.strip() else ""
        prompt = P.fill(P.GEN_HERO_COMPOSITE, space=space,
                        coords=json.dumps(coords)) + prev_ctx
        refs = list(ID.reference_images(root, n))
        return client.generate(prompt, refs, path)

    if etype == "cast_sheet":
        desc = meta.get("desc", "")
        angles = meta.get("angles", 7)
        return client.generate(P.fill(P.GEN_CHARACTER_SHEET, n=angles, desc=desc),
                               [], path)

    if etype == "cast_portrait":
        desc = meta.get("desc", "")
        return client.generate(P.fill(P.GEN_PORTRAIT, desc=desc), [], path)

    raise ValueError(f"element type '{etype}' does not support regeneration")
