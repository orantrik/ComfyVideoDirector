#!/usr/bin/env python3
"""
ArchViz Scene Director - the orchestrator.
==========================================

Drives the per-scene identity-lock pipeline. ComfyUI is the render engine
(NanoBanana image gen, Qwen3-VL analysis); this controller loops scenes and
variable item counts, fills the identity container, and (later phases) runs the
inspector gates + reporter and the video stage.

Run offline to scaffold + validate the whole container with placeholders:
    python archviz_director.py --project ./proj --frames ./screenshots --dry-run

Live (needs ComfyUI up + API-format recipe graphs in --recipes):
    python archviz_director.py --project ./proj --frames ./screenshots \
        --comfy-url http://127.0.0.1:8001 --recipes ./recipes --cast cast.json
"""

import os
import re
import sys
import glob
import json
import shutil
import base64
import argparse

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PKG_DIR)
from core import identity as ID            # noqa: E402
from core import prompts_archviz as P      # noqa: E402

# 1x1 PNG used as a placeholder asset in dry-run.
_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


# --------------------------------------------------------------------------- #
#  Clients (same interface; pick dry-run or live)
# --------------------------------------------------------------------------- #
class DryRunClient:
    label = "dry-run"

    def analyze(self, user_prompt, image_path, system_prompt=""):
        # Canned, structurally-valid responses so every pipeline stage exercises fully.
        # Order matters: more specific checks first.
        if "every small OBJECT" in user_prompt:
            return ("o01 | short whisky glass | clear cut crystal | on the coffee table, right side\n"
                    "o02 | ceramic vase | matte sage green | on the side table, left of sofa")
        if "piece of FURNITURE" in user_prompt:
            return ("f01 | grey 3-seat sofa | charcoal boucle fabric, low oak legs | "
                    "centre of lounge, facing the window wall\n"
                    "f02 | walnut coffee table | round walnut top, black metal base | "
                    "in front of the sofa")
        if "For EVERY visible element" in user_prompt:
            # Stage G coordinates — full element list with x/y positions.
            return json.dumps([
                {"id": "f01", "label": "sofa",         "kind": "furniture", "x": 0.50, "y": 0.60, "area": "lounge"},
                {"id": "f02", "label": "coffee table",  "kind": "furniture", "x": 0.50, "y": 0.72, "area": "lounge"},
                {"id": "o01", "label": "whisky glass",  "kind": "object",    "x": 0.55, "y": 0.74, "area": "lounge"},
                {"id": "o02", "label": "ceramic vase",  "kind": "object",    "x": 0.30, "y": 0.65, "area": "lounge"},
                {"id": "spokesman", "label": "spokesman", "kind": "person",  "x": 0.40, "y": 0.50, "area": "lounge"},
                {"id": "a01",       "label": "actor a01", "kind": "person",  "x": 0.65, "y": 0.55, "area": "lounge"},
            ])
        if "QA inspector" in user_prompt or "Compare the GENERATED" in user_prompt:
            # Stage K inspector — extract the label from the prompt.
            m = re.search(r"for '([^']+)'", user_prompt)
            label = m.group(1) if m else "element"
            return json.dumps({"label": label, "score": 95,
                               "hallucinations": [], "notes": "matches reference (dry-run)"})
        if "Write a single cinematic master prompt" in user_prompt:
            return ("A wide cinematic shot of the open-plan lounge bathed in warm afternoon light. "
                    "The spokesman stands near the walnut coffee table, the whisky glass resting to "
                    "her right. The space feels calm, intentional, curated.")
        if "voiceover narration" in user_prompt.lower():
            return ("She moves through the space with quiet confidence, each detail placed with "
                    "intention. The room speaks before anyone does.")
        if "continuity supervisor" in user_prompt or "Return only the rewritten prompt" in user_prompt:
            return ("The spokesman lifts the short crystal whisky glass from the right side of the "
                    "walnut coffee table and brings it to her lips, her gaze resting on the window wall.")
        if "spatial" in user_prompt or "space" in user_prompt.lower():
            return ("Open-plan lounge, ~6.0 x 5.0 x 2.8 m. Zones: lounge area (centre), "
                    "window wall (far side, full-height glazing), entry (near-left).")
        return "OK"

    def generate(self, prompt, ref_paths, out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as fh:
            fh.write(_PLACEHOLDER_PNG)
        return out_path


class ComfyClient:
    label = "comfyui"

    def __init__(self, comfy_url, recipes_dir):
        from core import comfy_api
        self.api = comfy_api
        self.url = comfy_url
        self.recipes = recipes_dir
        # Expected recipe files + the node ids the controller patches.
        self.qwen_recipe = os.path.join(recipes_dir, "qwen_analyze.json")
        self.nb_recipe = os.path.join(recipes_dir, "nanobanana_gen.json")
        # These node-id maps must match the exported recipe graphs:
        self.qwen_nodes = {"image": None, "user_prompt": None, "text_out": None}
        self.nb_nodes = {"prompt": None, "image_1": None, "image_2": None, "save": None}

    def analyze(self, user_prompt, image_path, system_prompt=""):
        up = self.api.upload_image(self.url, image_path)
        patches = {}
        if self.qwen_nodes["image"]:
            patches[self.qwen_nodes["image"]] = {"image": up}
        if self.qwen_nodes["user_prompt"]:
            patches[self.qwen_nodes["user_prompt"]] = {"user_prompt": user_prompt}
        return self.api.run_recipe_text(self.url, self.qwen_recipe, patches,
                                        text_node=self.qwen_nodes["text_out"])

    def generate(self, prompt, ref_paths, out_path):
        patches = {}
        if self.nb_nodes["prompt"]:
            patches[self.nb_nodes["prompt"]] = {"prompt": prompt}
        for slot, key in (("image_1", "image_1"), ("image_2", "image_2")):
            if self.nb_nodes.get(slot) and ref_paths:
                up = self.api.upload_image(self.url, ref_paths.pop(0))
                patches[self.nb_nodes[slot]] = {"image": up}
        return self.api.run_recipe(self.url, self.nb_recipe, patches, out_path)


# --------------------------------------------------------------------------- #
#  Parsing
# --------------------------------------------------------------------------- #
def parse_items(text):
    """Parse 'id | name | desc | location' lines into dicts."""
    items = []
    for line in (text or "").splitlines():
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        items.append({
            "id": parts[0], "name": parts[1] if len(parts) > 1 else parts[0],
            "desc": parts[2] if len(parts) > 2 else "",
            "location": parts[3] if len(parts) > 3 else "",
        })
    return items


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _format_cast(cast_info):
    lines = []
    sp = cast_info.get("spokesman", {})
    if sp:
        lines.append("Spokesman: " + ID.read_text(sp.get("desc_path", ""), "no desc"))
    for aid, a in cast_info.get("actors", {}).items():
        lines.append(f"Actor {aid}: " + ID.read_text(a.get("desc_path", ""), "no desc"))
    return "\n".join(lines) or "none"


def _format_inventory(root, n, furniture, objects):
    """Human-readable inventory string used in reconciliation prompts."""
    lines = []
    for it in furniture:
        lines.append(f"[furniture] {it['id']} '{it['name']}': {it['desc']} — {it['location']}")
    for it in objects:
        lines.append(f"[object] {it['id']} '{it['name']}': {it['desc']} — {it['location']}")
    cast = ID.load_index(root).get("cast", {})
    sp = cast.get("spokesman", {})
    if sp:
        lines.append("[person] spokesman: " + ID.read_text(sp.get("desc_path", ""), "no desc"))
    for aid, a in cast.get("actors", {}).items():
        lines.append(f"[person] {aid}: " + ID.read_text(a.get("desc_path", ""), "no desc"))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  Stages — Phase 1: A–F
# --------------------------------------------------------------------------- #
def stage_ingest(root, n, src_png):
    d = ID.init_scene(root, n)
    dst = os.path.join(d, "source.png")
    if os.path.abspath(src_png) != os.path.abspath(dst):
        import shutil
        shutil.copyfile(src_png, dst)
    return dst


def stage_analyze(root, n, src, client):
    d = ID.scene_dir(root, n)
    furn = client.analyze(P.ANALYSIS_FURNITURE, src)
    objs = client.analyze(P.ANALYSIS_OBJECTS, src)
    space = client.analyze(P.ANALYSIS_SPACE, src)
    ID.write_text(os.path.join(d, "identity", "furniture", "_list.txt"), furn)
    ID.write_text(os.path.join(d, "identity", "objects", "_list.txt"), objs)
    ID.write_json(os.path.join(d, "identity", "space.json"), {"description": space})
    return parse_items(furn), parse_items(objs), space


def stage_packshots(root, n, furniture, objects, client):
    for kind, items, dirfn in (("furniture", furniture, ID.furniture_item_dir),
                               ("objects", objects, ID.object_item_dir)):
        for it in items:
            idir = dirfn(root, n, it["id"])
            ID.write_text(os.path.join(idir, "desc.txt"),
                          f"{it['name']} | {it['desc']} | {it['location']}")
            packshot = os.path.join(idir, "packshot_4view.png")
            client.generate(P.fill(P.GEN_PACKSHOT_4VIEW,
                                   desc=f"{it['name']}, {it['desc']}"), [], packshot)
            ID.register_scene_item(root, n, kind, it["id"], {
                "name": it["name"], "desc_path": os.path.join(idir, "desc.txt"),
                "packshot": packshot, "location": it["location"]})


def stage_space_map(root, n, space, client):
    out = os.path.join(ID.scene_dir(root, n), "identity", "space_map.png")
    client.generate(P.fill(P.GEN_SPACE_MAP, desc=space), [], out)
    return out


def stage_stabilized_space(root, n, space, client):
    """Stage D: generate the clean empty-space hero conditioned on all packshots."""
    refs = ID.reference_images(root, n)  # all packshots written so far
    out = os.path.join(ID.scene_dir(root, n), "renders", "empty_space_stabilized.png")
    client.generate(P.fill(P.GEN_EMPTY_SPACE_STABILIZED, space=space), refs, out)
    ID.register_scene_space(root, n, {
        "desc_path": os.path.join(ID.scene_dir(root, n), "identity", "space.json"),
        "space_map": os.path.join(ID.scene_dir(root, n), "identity", "space_map.png"),
        "stabilized": out,
    })
    return out


def stage_cast(root, cast_spec, client):
    ID.init_project(root)
    sp = cast_spec.get("spokesman")
    if sp:
        sdir = os.path.join(ID.cast_dir(root), "spokesman")
        ID.write_text(os.path.join(sdir, "desc.txt"), sp["desc"])
        sheet = os.path.join(sdir, "sheet_7angle.png")
        portrait = os.path.join(sdir, "portrait.png")
        client.generate(P.fill(P.GEN_CHARACTER_SHEET, n=7, desc=sp["desc"]), [], sheet)
        client.generate(P.fill(P.GEN_PORTRAIT, desc=sp["desc"]), [], portrait)
        for i, garment in enumerate(sp.get("clothes", []), 1):
            cp = os.path.join(sdir, "clothes_packshots", f"garment_{i:02d}.png")
            client.generate(P.fill(P.GEN_CLOTHES_PACKSHOT, desc=garment), [], cp)
        ID.register_cast(root, "spokesman", "spokesman",
                         {"desc_path": os.path.join(sdir, "desc.txt"),
                          "sheet": sheet, "portrait": portrait})
    for actor in cast_spec.get("actors", []):
        aid = actor["id"]
        adir = os.path.join(ID.cast_dir(root), "actors", aid)
        ID.write_text(os.path.join(adir, "desc.txt"), actor["desc"])
        sheet = os.path.join(adir, "sheet.png")
        portrait = os.path.join(adir, "portrait.png")
        client.generate(P.fill(P.GEN_CHARACTER_SHEET, n=5, desc=actor["desc"]), [], sheet)
        client.generate(P.fill(P.GEN_PORTRAIT, desc=actor["desc"]), [], portrait)
        ID.register_cast(root, "actors", aid,
                         {"desc_path": os.path.join(adir, "desc.txt"),
                          "sheet": sheet, "portrait": portrait})


# --------------------------------------------------------------------------- #
#  Stages — Phase 2: G, I, J, K, L
# --------------------------------------------------------------------------- #
def stage_coordinates(root, n, src, client):
    """Stage G (part 1): Qwen3-VL extracts x/y/area coordinates for every element."""
    raw = client.analyze(P.ANALYSIS_COORDINATES, src)
    try:
        coords = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        coords = json.loads(m.group(0)) if m else []
    path = os.path.join(ID.scene_dir(root, n), "coordinates.json")
    ID.write_json(path, coords)
    return coords


def stage_master_prompt(root, n, furniture, objects, space, client):
    """Stage G (part 2): master prompt for the next scene + audio prompt."""
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    cast_text = _format_cast(ID.load_index(root).get("cast", {}))
    furn_text = "\n".join(f"{it['id']} | {it['name']} | {it['desc']} | {it['location']}"
                          for it in furniture)
    obj_text  = "\n".join(f"{it['id']} | {it['name']} | {it['desc']} | {it['location']}"
                          for it in objects)
    master = client.analyze(
        P.fill(P.MASTER_PROMPT, space=space, furniture=furn_text,
               objects=obj_text, cast=cast_text), src)
    audio = client.analyze(
        "Write a 2–3 sentence voiceover narration for a luxury ArchViz branded film "
        "based on this scene description. Elegant, third person, concise: " + master[:400],
        src)
    prompts_dir = os.path.join(ID.scene_dir(root, n), "prompts")
    ID.write_text(os.path.join(prompts_dir, "master_prompt.txt"), master)
    ID.write_text(os.path.join(prompts_dir, "audio_prompt.txt"), audio)
    return master, audio


def stage_hero_composite(root, n, space, coords, client):
    """Stage I: generate 4 hero-composite variations (space + cast + coords)."""
    refs = list(ID.reference_images(root, n))
    coords_str = json.dumps(coords)
    prompt = P.fill(P.GEN_HERO_COMPOSITE, space=space, coords=coords_str)
    variations = []
    for v in range(1, 5):
        out = os.path.join(ID.scene_dir(root, n), "renders", f"hero_v{v}.png")
        client.generate(prompt, list(refs), out)   # fresh copy of refs each call
        variations.append(out)
    return variations


def stage_reconcile(root, n, furniture, objects, coords, client):
    """Stage J: reconcile positive prompt so every action binds to a real element."""
    from nodes.object_lock import AIDirectorObjectLock
    src = os.path.join(ID.scene_dir(root, n), "source.png")
    master = ID.read_text(os.path.join(ID.scene_dir(root, n), "prompts", "master_prompt.txt"), "")
    inventory = _format_inventory(root, n, furniture, objects)
    rewritten = client.analyze(
        P.fill(P.RECONCILE, inventory=inventory,
               coords=json.dumps(coords), prompt=master), src)
    scene_objects = "; ".join(f"{it['name']} ({it['location']})"
                              for it in furniture + objects)
    positive, negative = AIDirectorObjectLock().amend(rewritten, "strict",
                                                      scene_objects=scene_objects)
    prompts_dir = os.path.join(ID.scene_dir(root, n), "prompts")
    ID.write_text(os.path.join(prompts_dir, "positive.txt"), positive)
    ID.write_text(os.path.join(prompts_dir, "negative.txt"), negative)
    return positive, negative


def stage_inspect(root, n, furniture, objects, variations, client):
    """Stage K: score every element in each hero variation; gate ≥90; emit report.json."""
    idx = ID.load_index(root)
    cast = idx.get("cast", {})
    elements = []
    for it in furniture:
        elements.append({"id": it["id"], "label": it["name"], "desc": it["desc"]})
    for it in objects:
        elements.append({"id": it["id"], "label": it["name"], "desc": it["desc"]})
    sp = cast.get("spokesman", {})
    if sp:
        elements.append({"id": "spokesman", "label": "spokesman",
                         "desc": ID.read_text(sp.get("desc_path", ""), "spokesman")})
    for aid, a in cast.get("actors", {}).items():
        elements.append({"id": aid, "label": aid,
                         "desc": ID.read_text(a.get("desc_path", ""), aid)})

    var_results = []
    best_idx, best_avg = 0, -1.0
    for vi, hero_path in enumerate(variations, 1):
        scores, hallu_all = [], []
        for el in elements:
            raw = client.analyze(
                P.fill(P.INSPECTOR, label=el["label"], desc=el["desc"]), hero_path)
            try:
                parsed = json.loads(raw)
                score = int(parsed.get("score", 0))
                hallu_all.extend(parsed.get("hallucinations", []))
            except (json.JSONDecodeError, TypeError, ValueError):
                score = 0
            scores.append({"element": el["id"], "label": el["label"], "score": score})
        avg = sum(s["score"] for s in scores) / len(scores) if scores else 0.0
        min_score = min((s["score"] for s in scores), default=0)
        var_results.append({
            "variation": f"hero_v{vi}.png",
            "path": hero_path,
            "scores": scores,
            "average": round(avg, 1),
            "min_score": min_score,
            "passes_gate": min_score >= 90,
            "hallucinations": hallu_all,
        })
        if avg > best_avg:
            best_avg, best_idx = avg, vi - 1

    # Prefer first variation that fully passes; fall back to highest average.
    for i, vr in enumerate(var_results):
        if vr["passes_gate"]:
            best_idx = i
            break

    report = {
        "scene": ID.scene_id(n),
        "gate_threshold": 90,
        "variations": var_results,
        "best_variation": var_results[best_idx]["variation"],
        "best_passes_gate": var_results[best_idx]["passes_gate"],
    }
    ID.write_json(os.path.join(ID.scene_dir(root, n), "report.json"), report)
    return report, best_idx


def stage_lock_hero(root, n, variations, best_idx, report):
    """Stage L: copy the chosen passing variation to hero_locked.png."""
    locked = os.path.join(ID.scene_dir(root, n), "renders", "hero_locked.png")
    shutil.copyfile(variations[best_idx], locked)
    vr = report["variations"][best_idx]
    status = "PASS" if vr["passes_gate"] else f"BEST_AVAILABLE avg={vr['average']:.0f}%"
    print(f"  hero_locked.png <- {vr['variation']} [{status}]")
    return locked


# --------------------------------------------------------------------------- #
#  Scene runner (phases 1 + 2 together)
# --------------------------------------------------------------------------- #
def run_scene(root, n, src, client, phases=(1, 2)):
    print(f"\n=== SCENE {n} ({client.label}) ===")

    if 1 in phases:
        src = stage_ingest(root, n, src)
        furniture, objects, space = stage_analyze(root, n, src, client)
        print(f"  furniture: {len(furniture)}  objects: {len(objects)}")
        stage_packshots(root, n, furniture, objects, client)
        stage_space_map(root, n, space, client)
        print("  packshots + space map done (Stages B–C)")
        stage_stabilized_space(root, n, space, client)
        print("  stabilized space done (Stage D)")
    else:
        # Phase 2 re-run: re-read what Phase 1 wrote.
        src = os.path.join(ID.scene_dir(root, n), "source.png")
        d = ID.scene_dir(root, n)
        furn_raw = ID.read_text(os.path.join(d, "identity", "furniture", "_list.txt"))
        obj_raw  = ID.read_text(os.path.join(d, "identity", "objects",   "_list.txt"))
        space    = ID.read_json(os.path.join(d, "identity", "space.json"),
                                {}).get("description", "")
        furniture, objects = parse_items(furn_raw), parse_items(obj_raw)

    if 2 in phases:
        coords = stage_coordinates(root, n, src, client)
        print(f"  coordinates: {len(coords)} elements (Stage G)")
        master, _ = stage_master_prompt(root, n, furniture, objects, space, client)
        print("  master prompt + audio prompt done (Stage G)")
        variations = stage_hero_composite(root, n, space, coords, client)
        print(f"  hero composite: {len(variations)} variations (Stage I)")
        stage_reconcile(root, n, furniture, objects, coords, client)
        print("  prompts reconciled (Stage J)")
        report, best_idx = stage_inspect(root, n, furniture, objects, variations, client)
        vr = report["variations"][best_idx]
        gate = "PASS" if vr["passes_gate"] else f"best={vr['average']:.0f}%"
        print(f"  inspector done [{gate}] (Stage K)")
        locked = stage_lock_hero(root, n, variations, best_idx, report)

    return {"furniture": furniture, "objects": objects, "space": space}


DEFAULT_CAST = {
    "spokesman": {"desc": "a woman in her early 30s, golden-brown wavy hair, tailored "
                          "cream trench coat, warm confident presence",
                  "clothes": ["cream tailored trench coat"]},
    "actors": [{"id": "a01", "desc": "a man in his 40s, short dark hair, navy suit"}],
}


def main():
    ap = argparse.ArgumentParser(description="ArchViz Scene Director")
    ap.add_argument("--project", required=True, help="project root folder")
    ap.add_argument("--frames", required=True, help="folder of scene screenshots")
    ap.add_argument("--scene", type=int, default=0, help="single scene number (0 = all)")
    ap.add_argument("--cast", default="", help="cast spec JSON (spokesman + actors)")
    ap.add_argument("--comfy-url", default="http://127.0.0.1:8001")
    ap.add_argument("--recipes", default="", help="dir of API-format recipe graphs")
    ap.add_argument("--dry-run", action="store_true", help="offline scaffold with placeholders")
    ap.add_argument("--phases", default="1,2",
                    help="comma-separated phases to run, e.g. '1' or '1,2' (default: 1,2)")
    args = ap.parse_args()

    phases = set(int(p.strip()) for p in args.phases.split(",") if p.strip())

    ID.init_project(args.project)
    cast_spec = ID.read_json(args.cast, DEFAULT_CAST) if args.cast else DEFAULT_CAST

    if args.dry_run or not args.recipes:
        client = DryRunClient()
        if not args.dry_run:
            print("[note] no --recipes given; running dry-run.")
    else:
        client = ComfyClient(args.comfy_url, args.recipes)

    if 1 in phases:
        stage_cast(args.project, cast_spec, client)
        print(f"cast locked ({client.label})")

    frames = sorted(f for f in glob.glob(os.path.join(args.frames, "*"))
                    if f.lower().endswith(IMAGE_EXTS))
    if not frames:
        sys.exit(f"no screenshots found in {args.frames}")
    if args.scene:
        frames = [frames[args.scene - 1]]
        scene_nums = [args.scene]
    else:
        scene_nums = list(range(1, len(frames) + 1))

    for n, src in zip(scene_nums, frames):
        run_scene(args.project, n, src, client, phases=phases)

    print(f"\nDONE. Identity container at: {args.project}")
    print(f"Index: {ID.index_path(args.project)}")


if __name__ == "__main__":
    main()
