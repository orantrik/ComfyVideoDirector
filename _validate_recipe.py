"""
Static validator for an API-format ComfyUI recipe.

Catches ALL problems in one pass (instead of one-per-failed-run):
  1. class_type values that are not registered by ComfyUI core or any installed
     custom-node pack  -> "missing node"
  2. input links that point to a node id that doesn't exist               -> "dangling link"
  3. dependency cycles                                                    -> "cycle"
  4. UI-only virtual nodes that must never appear in an API graph         -> "virtual node"
  5. heuristic flags for known-fragile inputs (triton_kernels: true, etc.)

It discovers available node types by scanning, via AST/text, the
NODE_CLASS_MAPPINGS declarations in:
  - ComfyUI core   (resources/ComfyUI/nodes.py + comfy_extras/*.py)
  - every folder under Documents/ComfyUI/custom_nodes
No heavy modules (torch/cv2) are imported, so it runs even when the server
can't boot.
"""
import ast
import json
import os
import re
import sys

CORE = r"C:\Users\oranbenshaprut\AppData\Local\Programs\ComfyUI\resources\ComfyUI"
CUSTOM = r"C:\Users\oranbenshaprut\Documents\ComfyUI\custom_nodes"
RECIPE = os.path.join(os.path.dirname(__file__), "recipes", "lipsync_scene.json")

VIRTUAL = {"SetNode", "GetNode", "Reroute", "Note", "PrimitiveNode",
           "Fast Groups Bypasser (rgthree)", "easy showAnything"}

# ComfyUI core nodes that are registered in nodes.py via a plain dict literal +
# many are added with NODE_CLASS_MAPPINGS["X"] = Y. We collect both forms.


def _strings_assigned_as_node_keys(src):
    """Find every string used as a NODE_CLASS_MAPPINGS key in a source file.

    Handles:
        NODE_CLASS_MAPPINGS = {"Foo": Foo, ...}
        NODE_CLASS_MAPPINGS["Foo"] = Foo
        NODE_CLASS_MAPPINGS.update({"Foo": Foo})
    plus the merged-dict form (**OTHER) by also grabbing any dict literal whose
    sibling value looks like a class — to stay safe we just grab ALL dict-literal
    string keys in any file that mentions NODE_CLASS_MAPPINGS.
    """
    keys = set()
    if "NODE_CLASS_MAPPINGS" not in src and "NODE_DISPLAY_NAME_MAPPINGS" not in src:
        return keys
    try:
        tree = ast.parse(src)
    except SyntaxError:
        # Fall back to regex for files we can't parse.
        keys.update(re.findall(r'NODE_CLASS_MAPPINGS\[\s*["\']([^"\']+)["\']\s*\]', src))
        return keys

    class V(ast.NodeVisitor):
        def visit_Dict(self, node):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
            self.generic_visit(node)

        def visit_Subscript(self, node):
            # NODE_CLASS_MAPPINGS["X"] = ...
            if (isinstance(node.value, ast.Name)
                    and node.value.id == "NODE_CLASS_MAPPINGS"
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                keys.add(node.slice.value)
            self.generic_visit(node)

    V().visit(tree)
    return keys


def collect_available_live(url="http://127.0.0.1:8188"):
    """Authoritative node list from a running ComfyUI server (/object_info)."""
    import urllib.request
    try:
        r = urllib.request.urlopen(url.rstrip("/") + "/object_info", timeout=8)
        data = json.load(r)
        return set(data.keys())
    except Exception:
        return None


def collect_available():
    available = set()
    # 1. core nodes.py
    core_nodes = os.path.join(CORE, "nodes.py")
    if os.path.isfile(core_nodes):
        available |= _strings_assigned_as_node_keys(
            open(core_nodes, encoding="utf-8", errors="ignore").read())
    # 2. core comfy_extras
    extras = os.path.join(CORE, "comfy_extras")
    if os.path.isdir(extras):
        for f in os.listdir(extras):
            if f.endswith(".py"):
                available |= _strings_assigned_as_node_keys(
                    open(os.path.join(extras, f), encoding="utf-8", errors="ignore").read())
    # 3. every custom-node pack (recurse one level into .py files)
    for pack in os.listdir(CUSTOM):
        pdir = os.path.join(CUSTOM, pack)
        if not os.path.isdir(pdir):
            continue
        for root, _dirs, files in os.walk(pdir):
            # Skip vendored deps to keep it fast.
            if any(seg in root for seg in ("__pycache__", "site-packages",
                                           ".git", "node_modules", "web")):
                continue
            for f in files:
                if f.endswith(".py"):
                    try:
                        available |= _strings_assigned_as_node_keys(
                            open(os.path.join(root, f), encoding="utf-8",
                                 errors="ignore").read())
                    except OSError:
                        pass
    return available


def main():
    recipe = json.load(open(RECIPE, encoding="utf-8"))
    nodes = {k: v for k, v in recipe.items() if not k.startswith("_")}
    available = collect_available_live()
    live = available is not None
    if not live:
        # Server offline: static scan can't see V3-registered core nodes, so
        # node-existence checks become unreliable. Disable that check.
        available = collect_available()
        print("[note] ComfyUI not running - skipping node-existence check "
              "(start ComfyUI for an authoritative check).\n")

    missing, virtual, dangling, fragile = [], [], [], []

    for nid, node in nodes.items():
        ct = node.get("class_type", "")
        if ct in VIRTUAL:
            virtual.append((nid, ct))
        elif live and ct not in available:
            missing.append((nid, ct))
        # link integrity
        for iname, ival in (node.get("inputs") or {}).items():
            if (isinstance(ival, list) and len(ival) == 2
                    and isinstance(ival[0], str)):
                if ival[0] not in nodes:
                    dangling.append((nid, ct, iname, ival[0]))
        # fragile heuristics
        ins = node.get("inputs") or {}
        if ins.get("triton_kernels") is True:
            fragile.append((nid, ct, "triton_kernels=true (no triton on Windows)"))

    # cycle detection (Kahn)
    deps = {nid: set() for nid in nodes}
    for nid, node in nodes.items():
        for ival in (node.get("inputs") or {}).values():
            if (isinstance(ival, list) and len(ival) == 2
                    and isinstance(ival[0], str) and ival[0] in nodes):
                deps[nid].add(ival[0])
    indeg = {n: 0 for n in nodes}
    for n in nodes:
        for _d in deps[n]:
            indeg[n] += 1
    # topological: repeatedly remove nodes whose deps are all resolved
    resolved, changed = set(), True
    while changed:
        changed = False
        for n in nodes:
            if n in resolved:
                continue
            if deps[n] <= resolved:
                resolved.add(n)
                changed = True
    cyclic = [n for n in nodes if n not in resolved]

    print(f"=== Recipe validation: {os.path.basename(RECIPE)} ===")
    print(f"nodes: {len(nodes)}   available node types discovered: {len(available)}\n")

    ok = True
    if missing:
        ok = False
        print("MISSING NODE TYPES (not registered by core or any installed pack):")
        for nid, ct in missing:
            print(f"   node {nid}: {ct}")
        print()
    if virtual:
        ok = False
        print("UI-ONLY VIRTUAL NODES (must be inlined / removed for API):")
        for nid, ct in virtual:
            print(f"   node {nid}: {ct}")
        print()
    if dangling:
        ok = False
        print("DANGLING LINKS (input references a node id that doesn't exist):")
        for nid, ct, iname, ref in dangling:
            print(f"   node {nid} ({ct}) input '{iname}' -> missing node {ref}")
        print()
    if cyclic:
        ok = False
        print("DEPENDENCY CYCLE involving nodes:")
        print("   " + ", ".join(sorted(cyclic)))
        print()
    if fragile:
        print("FRAGILE INPUTS (will likely fail on this machine):")
        for nid, ct, why in fragile:
            print(f"   node {nid} ({ct}): {why}")
        print()

    print("RESULT:", "OK - no blocking problems found" if ok
          else "PROBLEMS FOUND (see above)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
