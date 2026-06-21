"""Quality-gated asset reuse.

Scans the project folder for already-generated elements, judges whether each one
is good enough to keep, and lets the pipeline SKIP regeneration of healthy assets.
Every skipped generation is one NanoBanana / Gemini credit saved on a re-run.

Two layers of judging:

  1. FREE structural quality (Pillow): the file exists, is a valid image, is large
     enough, isn't a blank / near-uniform frame, and isn't the tiny dry-run
     placeholder. This alone catches failed, corrupt and placeholder generations.

  2. OPTIONAL fit score: a vision model rates how well the element fits the scene
     (this costs credits, so it is off by default). Heroes reuse the inspector's
     existing report.json score for free.

Verdicts are cached in <project>/_asset_cache.json keyed by relative path + mtime
+ byte size, so replacing or editing a file automatically invalidates its verdict.
"""

import json
import os

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

MIN_BYTES = 2048    # a real generation is never a few hundred bytes
MIN_DIM   = 200     # generated assets are always at least a few hundred px
BLANK_STD = 4.0     # grayscale std-dev below this => blank / near-uniform frame


def image_quality(path):
    """FREE structural check. Returns (ok: bool, reason: str, meta: dict)."""
    if not os.path.isfile(path):
        return False, "missing", {}
    try:
        size = os.path.getsize(path)
    except OSError:
        return False, "unstatable", {}
    if size < MIN_BYTES:
        return False, f"too small on disk ({size}B)", {"bytes": size}
    try:
        from PIL import Image, ImageStat
    except Exception:
        return True, "ok (no PIL)", {"bytes": size}   # accept on size alone
    try:
        with Image.open(path) as im:
            im.load()
            w, h = im.size
            if min(w, h) < MIN_DIM:
                return False, f"too small ({w}x{h})", {"w": w, "h": h, "bytes": size}
            stat = ImageStat.Stat(im.convert("L"))
            std = stat.stddev[0] if stat.stddev else 0.0
            if std < BLANK_STD:
                return False, f"blank/uniform (std={std:.1f})", {"w": w, "h": h, "std": std}
            return True, "ok", {"w": w, "h": h, "std": round(std, 1), "bytes": size}
    except Exception as e:
        return False, f"unreadable ({e})", {"bytes": size}


class AssetCache:
    """Per-project ledger that decides whether an existing asset can be reused."""

    def __init__(self, root, enabled=True, min_score=70, rescore=False):
        self.root = root
        self.enabled = enabled
        self.min_score = min_score
        self.rescore = rescore
        self.path = os.path.join(root, "_asset_cache.json")
        self.ledger = {}
        self.stats = {"reused": 0, "regen": 0}
        self._load()

    # -- ledger I/O --------------------------------------------------------- #
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.ledger = json.load(fh)
        except (OSError, ValueError):
            self.ledger = {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.ledger, fh, indent=2)
        except OSError:
            pass

    def _key(self, path):
        try:
            return os.path.relpath(path, self.root).replace("\\", "/")
        except ValueError:
            return path.replace("\\", "/")

    def _sig(self, path):
        st = os.stat(path)
        return {"mtime": int(st.st_mtime), "bytes": st.st_size}

    # -- decisions ---------------------------------------------------------- #
    def should_skip(self, path, kind="asset", min_score=None, scorer=None):
        """True if an existing asset is good enough to reuse (skip generation)."""
        if not self.enabled:
            return False
        ok, reason, meta = image_quality(path)
        if not ok:
            return False
        key = self._key(path)
        sig = self._sig(path)
        thr = self.min_score if min_score is None else min_score
        cached = self.ledger.get(key)

        # Trust a cached verdict only while the bytes are unchanged.
        if cached and cached.get("bytes") == sig["bytes"] \
                and cached.get("mtime") == sig["mtime"]:
            score = cached.get("score")
            if score is None or score >= thr:
                self.stats["reused"] += 1
                return True
            return False   # previously scored too low -> regenerate

        # New / changed file: it passed the structural gate. Optionally pay for a
        # fresh fit score; otherwise accept on structural quality alone.
        score = None
        if scorer is not None and self.rescore:
            try:
                score = int(scorer(path))
            except Exception:
                score = None
        verdict = {**sig, "kind": kind, "quality": reason, **meta}
        if score is not None:
            verdict["score"] = score
        self.ledger[key] = verdict
        self._save()
        if score is not None and score < thr:
            return False
        self.stats["reused"] += 1
        return True

    def record(self, path, kind="asset", score=None):
        """Record a freshly generated asset so later runs can reuse it."""
        if not os.path.isfile(path):
            return
        _, reason, meta = image_quality(path)
        verdict = {**self._sig(path), "kind": kind, "quality": reason, **meta}
        if score is not None:
            verdict["score"] = int(score)
        self.ledger[self._key(path)] = verdict
        self.stats["regen"] += 1
        self._save()

    def hero_reusable(self, scene_dir, min_score=None):
        """True if a locked hero already exists and passed inspection.

        Lets a re-run skip the whole hero-composite + reconcile + inspect block
        (the most expensive Phase-2 work) when last time's result was good.
        """
        if not self.enabled:
            return False
        locked = os.path.join(scene_dir, "renders", "hero_locked.png")
        ok, _, _ = image_quality(locked)
        if not ok:
            return False
        thr = self.min_score if min_score is None else min_score
        try:
            with open(os.path.join(scene_dir, "report.json"), encoding="utf-8") as fh:
                rep = json.load(fh)
        except (OSError, ValueError):
            return False
        if rep.get("inspect_mode") == "off":
            return False   # never scored, can't vouch for fit
        best = rep.get("best_variation")
        for vr in rep.get("variations", []):
            if vr.get("variation") == best:
                return float(vr.get("average", 0)) >= thr
        return False

    # -- reporting ---------------------------------------------------------- #
    def scan(self, verbose=True):
        """Walk the project, judge every generated image, print a reuse report."""
        results = []
        for dirpath, _dirs, files in os.walk(self.root):
            for f in files:
                if f.lower().endswith(IMAGE_EXTS):
                    p = os.path.join(dirpath, f)
                    ok, reason, _ = image_quality(p)
                    results.append((self._key(p), ok, reason))
        good = sum(1 for _k, ok, _r in results if ok)
        bad = len(results) - good
        if verbose:
            print(f"[scan] {len(results)} existing images: "
                  f"{good} reusable, {bad} will be regenerated")
            for key, ok, reason in results:
                if not ok:
                    print(f"    [regen] {key} -> {reason}")
        return results

    def summary(self):
        return (f"[reuse] {self.stats['reused']} elements reused (credits saved), "
                f"{self.stats['regen']} generated")
