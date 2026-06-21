"""
tools/ui_to_api.py
Converts a ComfyUI UI-format workflow JSON to the API-format prompt dict
used by archviz_director.py recipes.

Usage:
    python tools/ui_to_api.py <workflow.json> [--out recipes/output.json] [--url http://127.0.0.1:8000]

ComfyUI must be running so the script can fetch node schemas from /object_info.

Key fixes vs naive converters:
  - Widget values are consumed for EVERY widget-capable schema input (even when linked).
    ComfyUI always serialises the default widget value into widgets_values alongside the link.
  - SetNode/GetNode virtual connections are traced through and inlined.
  - Unknown node types still get their linked inputs wired correctly.
  - Seed control widgets ('randomize'/'fixed'/...) are consumed but dropped.
  - Display-only nodes (easy showAnything, Fast Groups Bypasser) are skipped.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path


# ----- node-type classification -------------------------------------------

SKIP_TYPES = {"Note", "Reroute", "easy showAnything", "Fast Groups Bypasser (rgthree)"}
PRIMITIVE_TYPES = {
    "PrimitiveNode", "PrimitiveInt", "PrimitiveFloat",
    "PrimitiveString", "PrimitiveBool", "PrimitiveBoolean",
}
SET_NODE_TYPE = "SetNode"
GET_NODE_TYPE = "GetNode"

CONTROL_WIDGETS = {"randomize", "fixed", "increment", "decrement"}
SEED_NAMES = {"seed", "noise_seed"}

# Only these types produce a widget value entry in widgets_values.
# Everything else (MODEL, IMAGE, LATENT, PROJECT, SAMPLER, GUIDER, VAE, CLIP, …)
# is a socket — it NEVER consumes a slot in widgets_values.
KNOWN_WIDGET_TYPES = {
    "INT", "FLOAT", "STRING", "BOOLEAN", "BOOL",
    "COLOR", "IMAGEUPLOAD",
}


def is_widget_type(spec: list) -> bool:
    """Return True if a schema input will contribute a widget value to widgets_values."""
    if not spec:
        return True
    t = spec[0]
    if isinstance(t, list):
        return True   # COMBO (list of options) → always a widget
    if isinstance(t, str):
        return t.upper() in KNOWN_WIDGET_TYPES
    return False


# ----- ComfyUI helpers ------------------------------------------------------

def fetch_schema(comfy_url: str) -> dict:
    r = urllib.request.urlopen(f"{comfy_url}/object_info", timeout=30)
    return json.loads(r.read())


def ordered_schema_inputs(schema: dict) -> list[tuple[str, list]]:
    req = schema.get("input", {}).get("required", {})
    opt = schema.get("input", {}).get("optional", {})
    return list(req.items()) + list(opt.items())


# ----- main conversion ------------------------------------------------------

def convert(workflow: dict, all_schemas: dict) -> dict:
    raw_nodes: list[dict] = workflow.get("nodes", [])
    links_raw: list[list] = workflow.get("links", [])

    node_by_id: dict[str, dict] = {str(n["id"]): n for n in raw_nodes}

    # --- Step 1: raw link map -------------------------------------------
    # link_id -> [source_node_id_str, source_output_slot]
    # Primitive sources store their value directly as ("PRIMITIVE", value).
    link_map: dict[int, list] = {}
    for lk in links_raw:
        link_id, src_node_id, src_out_slot = lk[0], lk[1], lk[2]
        src_str = str(src_node_id)
        src_node = node_by_id.get(src_str, {})
        if src_node.get("type", "") in PRIMITIVE_TYPES:
            wv = src_node.get("widgets_values", [])
            val = wv[0] if wv else None
            link_map[link_id] = ("PRIMITIVE", val)
        else:
            link_map[link_id] = [src_str, src_out_slot]

    # --- Step 2: resolve SetNode / GetNode virtual connections -----------
    # SetNode stores a named value; GetNode retrieves it.
    # We trace: SetNode.input_link → var_name → GetNode.output_links
    # so that any link coming OUT of a GetNode resolves to the original source.

    # var_name -> resolved source (what SetNode receives on its first input)
    set_var_source: dict[str, list] = {}
    for n in raw_nodes:
        if n.get("type") != SET_NODE_TYPE:
            continue
        wv = n.get("widgets_values", [])
        var_name = str(wv[0]) if wv else ""
        ui_inputs = n.get("inputs", [])
        if ui_inputs:
            link_id = ui_inputs[0].get("link")
            if link_id is not None and link_id in link_map:
                set_var_source[var_name] = link_map[link_id]

    # get_node_id -> resolved source
    get_node_source: dict[str, list] = {}
    for n in raw_nodes:
        if n.get("type") != GET_NODE_TYPE:
            continue
        wv = n.get("widgets_values", [])
        var_name = str(wv[0]) if wv else ""
        source = set_var_source.get(var_name)
        if source:
            get_node_source[str(n["id"])] = source

    # Re-resolve link_map entries whose source is a GetNode
    for lk in links_raw:
        link_id, src_node_id = lk[0], str(lk[1])
        if src_node_id in get_node_source:
            link_map[link_id] = get_node_source[src_node_id]

    # --- Step 3: build API dict ------------------------------------------
    api: dict[str, dict] = {}

    for node in raw_nodes:
        node_type = node.get("type", "")
        node_id_str = str(node.get("id"))

        if node_type in SKIP_TYPES or node_type in PRIMITIVE_TYPES:
            continue
        if node_type in {SET_NODE_TYPE, GET_NODE_TYPE}:
            continue  # inlined above; don't emit as real nodes

        schema = all_schemas.get(node_type)
        known = schema is not None
        if not known:
            print(f"  [warn] unknown schema '{node_type}' (id={node_id_str}) — links wired, widgets skipped")
            schema = {}

        schema_inputs = ordered_schema_inputs(schema)
        ui_inputs: list[dict] = node.get("inputs", [])
        widgets_values: list = list(node.get("widgets_values", []))

        # Build quick lookup: input_name -> resolved link target
        ui_linked: dict[str, list] = {}
        for ui_inp in ui_inputs:
            link_id = ui_inp.get("link")
            if link_id is not None:
                resolved = link_map.get(link_id)
                if resolved is not None:
                    name = ui_inp.get("name", "")
                    if isinstance(resolved, tuple) and resolved[0] == "PRIMITIVE":
                        ui_linked[name] = resolved[1]   # inline the constant
                    else:
                        ui_linked[name] = resolved

        inputs: dict[str, object] = {}

        if known:
            # Process in schema order, consuming widgets_values correctly.
            for inp_name, spec in schema_inputs:
                wtype = is_widget_type(spec)

                if inp_name in ui_linked:
                    val = ui_linked[inp_name]
                    if val is not None:
                        inputs[inp_name] = val
                    # KEY FIX: widget-capable inputs always consume a slot in
                    # widgets_values even when linked (ComfyUI serialises the default).
                    if wtype and widgets_values:
                        _ = widgets_values.pop(0)
                        if (isinstance(spec[0], str)
                                and spec[0].upper() == "INT"
                                and inp_name in SEED_NAMES
                                and widgets_values
                                and widgets_values[0] in CONTROL_WIDGETS):
                            _ = widgets_values.pop(0)
                    continue

                if not wtype:
                    continue  # socket-only with no link → skip

                if widgets_values:
                    val = widgets_values.pop(0)
                    inputs[inp_name] = val
                    if (isinstance(spec[0], str)
                            and spec[0].upper() == "INT"
                            and inp_name in SEED_NAMES
                            and widgets_values
                            and widgets_values[0] in CONTROL_WIDGETS):
                        _ = widgets_values.pop(0)

        else:
            # Unknown schema: just wire all linked inputs by name from UI format.
            for ui_inp in ui_inputs:
                name = ui_inp.get("name", "")
                if name in ui_linked:
                    inputs[name] = ui_linked[name]
            # Append any widget values under positional keys for reference
            for i, wv in enumerate(widgets_values):
                inputs[f"_widget_{i}"] = wv

        title = node.get("title") or node_type
        api[node_id_str] = {
            "class_type": node_type,
            "inputs": inputs,
            "_meta": {"title": title},
        }

    return api


# ----- CLI -----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Convert ComfyUI UI-format workflow to API recipe JSON"
    )
    ap.add_argument("workflow", help="Path to UI-format workflow .json")
    ap.add_argument("--out", default="",
                    help="Output path (default: recipes/<basename>.json in project root)")
    ap.add_argument("--url", default="http://127.0.0.1:8000",
                    help="ComfyUI base URL (default: http://127.0.0.1:8000)")
    args = ap.parse_args()

    wf_path = Path(args.workflow)
    if not wf_path.exists():
        sys.exit(f"File not found: {wf_path}")

    workflow = json.loads(wf_path.read_text(encoding="utf-8", errors="replace"))
    node_count = len(workflow.get("nodes", []))
    print(f"Loaded {node_count} nodes from {wf_path.name}")

    print(f"Fetching schemas from {args.url} ...")
    try:
        all_schemas = fetch_schema(args.url)
    except Exception as e:
        sys.exit(
            f"Cannot reach ComfyUI at {args.url}: {e}\n"
            "Start ComfyUI Desktop, then re-run this script."
        )
    print(f"  -> {len(all_schemas)} node types in registry")

    print("Converting ...")
    api = convert(workflow, all_schemas)
    print(f"  -> {len(api)} API nodes")

    if args.out:
        out_path = Path(args.out)
    else:
        here = Path(__file__).resolve().parent.parent / "recipes"
        here.mkdir(exist_ok=True)
        out_path = here / wf_path.with_suffix(".json").name

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(api, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved -> {out_path}")

    # Print patch-point summary for director nodes
    patch_types = {
        "AIDirectorProjectSetup", "UnrealFrameIntake", "SegmentPromptPicker",
        "SaveVideo", "RandomNoise",
    }
    patch_nodes = {nid: v for nid, v in api.items() if v["class_type"] in patch_types}
    if patch_nodes:
        print()
        print("Key patch points for archviz_director.py:")
        for nid, v in patch_nodes.items():
            ct = v["class_type"]
            title = v.get("_meta", {}).get("title", "")
            scalar_inputs = {
                k: val for k, val in v.get("inputs", {}).items()
                if not isinstance(val, list)
            }
            print(f"  Node '{nid}' {ct} [{title}]")
            for k, val in scalar_inputs.items():
                print(f"      {k}: {repr(val)[:80]}")


if __name__ == "__main__":
    main()
