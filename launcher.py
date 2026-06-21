"""
ArchViz Director Launcher
=========================
One-click .exe that:
  1. Checks whether ComfyUI is running; launches it + opens the browser GUI.
  2. Runs any phase of the pipeline with a LIVE streaming log.
  3. Gallery tab: previews every generated element (cast, packshots, space,
     hero variations, …) as thumbnails, and lets you REGENERATE any single
     image in place if it came out wrong.
"""

import os
import sys
import time
import threading
import subprocess
import webbrowser
import urllib.request
import importlib.util
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

try:
    from PIL import Image, ImageTk
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

# ── locate bundled resources ────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BUNDLE_DIR = sys._MEIPASS
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    SCRIPT_DIR = BUNDLE_DIR

DIRECTOR_PY = os.path.join(BUNDLE_DIR, "archviz_director.py")
RECIPES_DIR = os.path.join(BUNDLE_DIR, "recipes")
COMFYUI_URL = "http://127.0.0.1:8000"
COMFYUI_EXE = r"C:\Users\oranbenshaprut\AppData\Local\Programs\comfyui\ComfyUI.exe"
COMFYUI_BAT = r"C:\Users\oranbenshaprut\Documents\ComfyUI\run_comfyui.bat"

THUMB = 190           # thumbnail max edge in px
COLS = 4              # gallery columns

# Make bundled package importable
if BUNDLE_DIR not in sys.path:
    sys.path.insert(0, BUNDLE_DIR)


# ── pipeline module loader (cached) ─────────────────────────────────────────
_PIPELINE_MOD = None


def load_pipeline():
    global _PIPELINE_MOD
    if _PIPELINE_MOD is None:
        spec = importlib.util.spec_from_file_location("archviz_director", DIRECTOR_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PIPELINE_MOD = mod
    return _PIPELINE_MOD


# ── ComfyUI helpers ─────────────────────────────────────────────────────────
def comfyui_is_running():
    try:
        urllib.request.urlopen(f"{COMFYUI_URL}/system_stats", timeout=3)
        return True
    except Exception:
        return False


def launch_comfyui():
    for candidate in (COMFYUI_EXE, COMFYUI_BAT):
        if os.path.isfile(candidate):
            subprocess.Popen([candidate], creationflags=subprocess.CREATE_NEW_CONSOLE)
            return candidate
    return None


# ── Main window ─────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ArchViz Director")
        self.minsize(900, 660)
        self.geometry("1040x760")

        self.var_project = tk.StringVar()
        self.var_frames = tk.StringVar()
        self.var_director_frames = tk.StringVar()
        self.var_phases = tk.StringVar(value="1,2,3,4,5")
        self.var_voice = tk.StringVar(value="af_heart")
        self.var_compose = tk.StringVar(value="hard_cut_reencode")
        self.var_scene_type = tk.StringVar(value="exterior")
        self.var_inspect = tk.StringVar(value="fast")
        self.var_maxpackshots = tk.StringVar(value="8")
        self.var_herovars = tk.StringVar(value="4")
        self.var_reuse = tk.BooleanVar(value=True)
        self.var_dryrun = tk.BooleanVar(value=False)
        self.var_token = tk.StringVar()
        self.var_model = tk.StringVar(value="Nano Banana 2 (Gemini 3.1 Flash Image)")

        self._running = False
        self._stop_flag = False
        self._thumb_refs = []          # keep PhotoImage refs alive
        self._client_cache = {}

        self._load_settings()
        self._build_status_bar()
        self._build_tabs()
        self._poll_comfyui_status()
        self._fetch_model_options()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── status bar ───────────────────────────────────────────────────────
    def _build_status_bar(self):
        bar = tk.Frame(self, bg="#1a1a2e")
        bar.pack(fill="x")
        self.status_dot = tk.Label(bar, text="\u25cf", font=("Arial", 14),
                                   bg="#1a1a2e", fg="grey")
        self.status_dot.pack(side="left", padx=(10, 4))
        self.status_lbl = tk.Label(bar, text="Checking ComfyUI\u2026", bg="#1a1a2e",
                                   fg="white", font=("Arial", 10, "bold"))
        self.status_lbl.pack(side="left")
        self.btn_comfy = tk.Button(bar, text="Open ComfyUI GUI", command=self._open_browser,
                                   bg="#0f3460", fg="white", relief="flat", padx=10,
                                   cursor="hand2")
        self.btn_comfy.pack(side="right", padx=10, pady=6)

    # ── tabs ─────────────────────────────────────────────────────────────
    def _build_tabs(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.tab_run = tk.Frame(nb)
        self.tab_gallery = tk.Frame(nb)
        nb.add(self.tab_run, text="  Run  ")
        nb.add(self.tab_gallery, text="  Gallery  ")
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._nb = nb
        self._build_run_tab()
        self._build_gallery_tab()

    # ── RUN TAB ──────────────────────────────────────────────────────────
    def _build_run_tab(self):
        p = self.tab_run

        folders = ttk.LabelFrame(p, text="  Folders  ", padding=10)
        folders.pack(fill="x", padx=12, pady=(10, 4))
        self._folder_row(folders, 0, "Project folder:", self.var_project)
        self._folder_row(folders, 1, "Screenshots (one per scene):", self.var_frames)
        self._folder_row(folders, 2, "Unreal frames (director mode, optional):",
                         self.var_director_frames)

        opts = ttk.LabelFrame(p, text="  Options  ", padding=10)
        opts.pack(fill="x", padx=12, pady=4)
        tk.Label(opts, text="Phases:").grid(row=0, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.var_phases, width=16).grid(
            row=0, column=1, sticky="w", padx=(4, 18))
        tk.Label(opts, text="Kokoro voice:").grid(row=0, column=2, sticky="w")
        ttk.Entry(opts, textvariable=self.var_voice, width=13).grid(
            row=0, column=3, sticky="w", padx=(4, 18))
        tk.Label(opts, text="Compose:").grid(row=0, column=4, sticky="w")
        ttk.Combobox(opts, textvariable=self.var_compose, width=18,
                     values=["hard_cut_reencode", "hard_cut_copy", "crossfade"],
                     state="readonly").grid(row=0, column=5, sticky="w", padx=4)
        tk.Label(opts, text="Scene type:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(opts, textvariable=self.var_scene_type, width=13,
                     values=["exterior", "interior"],
                     state="readonly").grid(row=1, column=1, sticky="w",
                                             padx=(4, 18), pady=(6, 0))
        ttk.Checkbutton(opts, text="Dry-run (offline scaffold)",
                        variable=self.var_dryrun).grid(
            row=1, column=2, columnspan=4, sticky="w", pady=(6, 0))

        # API token + image model (for Gemini / NanoBanana API nodes)
        tk.Label(opts, text="API token:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.ent_token = ttk.Entry(opts, textvariable=self.var_token, width=34, show="\u2022")
        self.ent_token.grid(row=2, column=1, columnspan=2, sticky="we", padx=(4, 6), pady=(8, 0))
        self._token_shown = False
        ttk.Button(opts, text="show", width=5,
                   command=self._toggle_token).grid(row=2, column=3, sticky="w", pady=(8, 0))

        tk.Label(opts, text="Image model:").grid(row=2, column=4, sticky="w", pady=(8, 0))
        self.cmb_model = ttk.Combobox(opts, textvariable=self.var_model, width=30,
                                      values=[self.var_model.get()])
        self.cmb_model.grid(row=2, column=5, sticky="w", padx=4, pady=(8, 0))

        tk.Label(opts, text="(token only needed if ComfyUI is not logged in to a "
                            "ComfyOrg account)", fg="#888").grid(
            row=3, column=0, columnspan=6, sticky="w", pady=(2, 0))

        # Cost controls — each NanoBanana/Gemini call costs API credits.
        cost = ttk.LabelFrame(p, text="  Cost controls (API credits)  ", padding=10)
        cost.pack(fill="x", padx=12, pady=4)
        tk.Label(cost, text="QA inspect:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(cost, textvariable=self.var_inspect, width=8,
                     values=["off", "fast", "full"], state="readonly").grid(
            row=0, column=1, sticky="w", padx=(4, 18))
        tk.Label(cost, text="Max packshots:").grid(row=0, column=2, sticky="w")
        ttk.Entry(cost, textvariable=self.var_maxpackshots, width=6).grid(
            row=0, column=3, sticky="w", padx=(4, 18))
        tk.Label(cost, text="Hero variations:").grid(row=0, column=4, sticky="w")
        ttk.Entry(cost, textvariable=self.var_herovars, width=6).grid(
            row=0, column=5, sticky="w", padx=4)
        tk.Label(cost, text="off = no QA scoring (cheapest) \u00b7 fast = 1 call/variation "
                            "\u00b7 full = 1 call/element (most $$)", fg="#888").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(2, 0))
        ttk.Checkbutton(cost, text="Reuse existing good elements (scan & skip "
                                   "regen \u2014 saves credits on re-runs)",
                        variable=self.var_reuse).grid(
            row=2, column=0, columnspan=6, sticky="w", pady=(4, 0))

        btns = tk.Frame(p)
        btns.pack(fill="x", padx=12, pady=6)
        self.btn_run = tk.Button(btns, text="\u25b6  Run Pipeline", command=self._run_pipeline,
                                 bg="#16213e", fg="white", font=("Arial", 11, "bold"),
                                 relief="flat", padx=18, pady=8, cursor="hand2")
        self.btn_run.pack(side="left")
        tk.Button(btns, text="\u2716  Stop", command=self._stop_pipeline, bg="#4a0000",
                  fg="white", relief="flat", padx=12, pady=8, cursor="hand2").pack(
            side="left", padx=8)
        tk.Button(btns, text="\U0001f5d1  Clear log",
                  command=lambda: self.log.delete("1.0", "end"), bg="#2a2a2a",
                  fg="white", relief="flat", padx=12, pady=8, cursor="hand2").pack(
            side="right")

        logf = ttk.LabelFrame(p, text="  Live pipeline log  ", padding=4)
        logf.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        self.log = scrolledtext.ScrolledText(logf, bg="#0d1117", fg="#c9d1d9",
                                             font=("Consolas", 9), wrap="word")
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("err", foreground="#ff6b6b")
        self.log.tag_config("ok", foreground="#56d364")
        self.log.tag_config("info", foreground="#79c0ff")

    def _folder_row(self, parent, row, label, var):
        tk.Label(parent, text=label, anchor="w").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=var, width=52).grid(row=row, column=1, sticky="ew", padx=6)
        tk.Button(parent, text="\u2026", width=3, cursor="hand2",
                  command=lambda v=var: v.set(
                      filedialog.askdirectory(title="Select folder") or v.get())
                  ).grid(row=row, column=2)
        parent.columnconfigure(1, weight=1)

    # ── GALLERY TAB ──────────────────────────────────────────────────────
    def _build_gallery_tab(self):
        p = self.tab_gallery

        top = tk.Frame(p)
        top.pack(fill="x", padx=12, pady=8)
        tk.Label(top, text="Previews of every generated element. "
                           "Click an image to open it; Regenerate to remake just that one.",
                 anchor="w", fg="#444").pack(side="left")
        tk.Button(top, text="\u21bb  Refresh", command=self.refresh_gallery,
                  bg="#16213e", fg="white", relief="flat", padx=12, pady=4,
                  cursor="hand2").pack(side="right")

        # scrollable canvas
        wrap = tk.Frame(p)
        wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.gcanvas = tk.Canvas(wrap, bg="#fafafa", highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.gcanvas.yview)
        self.gcanvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.gcanvas.pack(side="left", fill="both", expand=True)
        self.ginner = tk.Frame(self.gcanvas, bg="#fafafa")
        self._ginner_id = self.gcanvas.create_window((0, 0), window=self.ginner, anchor="nw")
        self.ginner.bind("<Configure>",
                         lambda e: self.gcanvas.configure(scrollregion=self.gcanvas.bbox("all")))
        self.gcanvas.bind("<Configure>",
                          lambda e: self.gcanvas.itemconfig(self._ginner_id, width=e.width))
        self.gcanvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self._gallery_loaded_for = None

    def _on_mousewheel(self, event):
        # only scroll when the gallery tab is active
        if self._nb.index(self._nb.select()) == 1:
            self.gcanvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_tab_changed(self, _event):
        if self._nb.index(self._nb.select()) == 1:
            if self._gallery_loaded_for != self.var_project.get().strip():
                self.refresh_gallery()

    def refresh_gallery(self):
        for w in self.ginner.winfo_children():
            w.destroy()
        self._thumb_refs.clear()

        root = self.var_project.get().strip()
        if not root or not os.path.isdir(root):
            tk.Label(self.ginner, text="Set a Project folder on the Run tab, then Refresh.",
                     bg="#fafafa", fg="#888", font=("Arial", 11)).pack(pady=40)
            return

        try:
            from core import gallery as G
            groups = G.scan_project(root)
        except Exception as e:
            tk.Label(self.ginner, text=f"Scan failed: {e}", bg="#fafafa",
                     fg="#c00").pack(pady=40)
            return

        self._gallery_loaded_for = root
        any_items = False
        for grp in groups:
            items = [it for it in grp["items"] if it["exists"] or it["regen"]]
            if not items:
                continue
            any_items = True
            hdr = tk.Frame(self.ginner, bg="#eef0f4")
            hdr.pack(fill="x", pady=(10, 2))
            tk.Label(hdr, text=grp["title"], bg="#eef0f4", fg="#222",
                     font=("Arial", 12, "bold"), anchor="w").pack(side="left", padx=8, pady=4)

            grid = tk.Frame(self.ginner, bg="#fafafa")
            grid.pack(fill="x")
            for i, it in enumerate(items):
                self._build_card(grid, it, i)
            for c in range(COLS):
                grid.columnconfigure(c, weight=1)

        if not any_items:
            tk.Label(self.ginner, text="No elements generated yet. Run the pipeline first.",
                     bg="#fafafa", fg="#888", font=("Arial", 11)).pack(pady=40)

    def _build_card(self, parent, it, index):
        r, c = divmod(index, COLS)
        card = tk.Frame(parent, bg="white", bd=1, relief="solid")
        card.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")

        thumb = self._make_thumb(it)
        img_lbl = tk.Label(card, image=thumb, bg="white", cursor="hand2")
        img_lbl.image = thumb
        img_lbl.pack(padx=6, pady=(6, 2))
        if it["exists"]:
            img_lbl.bind("<Button-1>", lambda e, p=it["path"]: self._open_file(p))

        status = "\u2713" if it["exists"] else "\u2014 not generated"
        scolor = "#2a8c2a" if it["exists"] else "#b00"
        tk.Label(card, text=it["label"], bg="white", fg="#222", wraplength=THUMB,
                 font=("Arial", 8, "bold"), justify="center").pack(padx=4)
        tk.Label(card, text=status, bg="white", fg=scolor,
                 font=("Arial", 8)).pack()

        if it["regen"]:
            tk.Button(card, text="\u21bb Regenerate", relief="flat", bg="#0f3460",
                      fg="white", cursor="hand2", font=("Arial", 8),
                      command=lambda meta=it: self._regen(meta)).pack(
                fill="x", padx=6, pady=(2, 6))
        else:
            tk.Label(card, text=" ", bg="white").pack(pady=(2, 6))

    def _make_thumb(self, it):
        if HAVE_PIL and it["exists"]:
            try:
                im = Image.open(it["path"])
                im.thumbnail((THUMB, THUMB))
                ph = ImageTk.PhotoImage(im)
                self._thumb_refs.append(ph)
                return ph
            except Exception:
                pass
        # placeholder grey box
        ph = tk.PhotoImage(width=THUMB, height=int(THUMB * 0.62))
        ph.put("#e6e6e6", to=(0, 0, THUMB, int(THUMB * 0.62)))
        self._thumb_refs.append(ph)
        return ph

    def _open_file(self, path):
        try:
            os.startfile(path)   # noqa: Windows only
        except Exception:
            webbrowser.open("file://" + os.path.abspath(path))

    # ── regenerate a single element ──────────────────────────────────────
    def _make_client(self, dry):
        key = "dry" if dry else "live"
        mod = load_pipeline()
        client = self._client_cache.get(key)
        if client is None:
            client = mod.DryRunClient() if dry else mod.ComfyClient(COMFYUI_URL, RECIPES_DIR)
            self._client_cache[key] = client
        if not dry:
            # always apply the latest token / model from the GUI
            client.api_token = self.var_token.get().strip()
            client.image_model = self.var_model.get().strip() or client.image_model
        return client

    def _regen(self, meta):
        dry = self.var_dryrun.get()
        if not dry and not comfyui_is_running():
            if not messagebox.askyesno(
                    "ComfyUI offline",
                    "ComfyUI is not running. Regenerate in dry-run (placeholder) mode?"):
                return
            dry = True

        self._switch_to_run_log()
        self._log(f"[regen] {meta['label']} \u2192 {os.path.basename(meta['path'])}\n", "info")

        def work():
            try:
                from core import gallery as G
                from core import prompts_archviz as P
                # Match the project's scene type so regenerated packshots/heroes use
                # the same (interior vs exterior) prompt vocabulary as the full run.
                P.apply_scene_type(self.var_scene_type.get().strip() or "exterior")
                client = self._make_client(dry)
                out = G.regen_element(self.var_project.get().strip(), meta, client)
                self.after(0, self._log, f"[ok] regenerated: {out}\n", "ok")
                self.after(0, self.refresh_gallery)
            except Exception as e:
                import traceback
                self.after(0, self._log, traceback.format_exc(), "err")
                self.after(0, lambda: messagebox.showerror("Regenerate failed", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _switch_to_run_log(self):
        self._nb.select(self.tab_run)

    # ── ComfyUI status polling ───────────────────────────────────────────
    def _poll_comfyui_status(self):
        def poll():
            while True:
                running = comfyui_is_running()
                self.after(0, self._update_status, running)
                time.sleep(5)
        threading.Thread(target=poll, daemon=True).start()

    def _update_status(self, running):
        if running:
            self.status_dot.config(fg="#56d364")
            self.status_lbl.config(text="ComfyUI is running  \u2022  " + COMFYUI_URL)
            self.btn_comfy.config(text="Open ComfyUI GUI \u2197")
        else:
            self.status_dot.config(fg="#ff6b6b")
            self.status_lbl.config(text="ComfyUI is NOT running")
            self.btn_comfy.config(text="Launch + Open ComfyUI")

    def _open_browser(self):
        if comfyui_is_running():
            webbrowser.open(COMFYUI_URL)
            return
        path = launch_comfyui()
        if path:
            self._log(f"[info] Starting ComfyUI from: {path}\n", "info")
            self._log("[info] Waiting for ComfyUI to become ready\u2026\n", "info")
            threading.Thread(target=self._wait_and_open, daemon=True).start()
        else:
            messagebox.showwarning(
                "ComfyUI not found",
                "Could not find ComfyUI Desktop or run_comfyui.bat.\n"
                "Start ComfyUI manually, then click Open ComfyUI GUI.")

    def _wait_and_open(self):
        for _ in range(60):
            time.sleep(2)
            if comfyui_is_running():
                webbrowser.open(COMFYUI_URL)
                self.after(0, self._log, "[ok] ComfyUI is up \u2014 browser opened.\n", "ok")
                return
        self.after(0, self._log, "[err] ComfyUI did not start within 2 minutes.\n", "err")

    # ── token / model / settings ─────────────────────────────────────────
    def _toggle_token(self):
        self._token_shown = not self._token_shown
        self.ent_token.config(show="" if self._token_shown else "\u2022")

    def _fetch_model_options(self):
        """Populate the model dropdown live from the GeminiNanoBanana2V2 node."""
        def work():
            try:
                import json as _json
                url = f"{COMFYUI_URL}/object_info/GeminiNanoBanana2V2"
                info = _json.loads(urllib.request.urlopen(url, timeout=6).read())
                node = info["GeminiNanoBanana2V2"]
                spec = node["input"]["required"]["model"][1]
                opts = [o["key"] for o in spec.get("options", []) if "key" in o]
                if opts:
                    self.after(0, lambda: self.cmb_model.config(values=opts))
                    if self.var_model.get() not in opts:
                        self.after(0, lambda: self.var_model.set(opts[0]))
            except Exception:
                pass   # offline / node missing — keep the default value
        threading.Thread(target=work, daemon=True).start()

    def _settings_path(self):
        return os.path.join(os.path.expanduser("~"), ".archviz_director.json")

    def _load_settings(self):
        try:
            import json as _json
            with open(self._settings_path(), encoding="utf-8") as fh:
                s = _json.load(fh)
            self.var_project.set(s.get("project", ""))
            self.var_frames.set(s.get("frames", ""))
            self.var_director_frames.set(s.get("director_frames", ""))
            self.var_phases.set(s.get("phases", "1,2,3,4,5"))
            self.var_voice.set(s.get("voice", "af_heart"))
            self.var_compose.set(s.get("compose", "hard_cut_reencode"))
            self.var_scene_type.set(s.get("scene_type", "exterior"))
            self.var_inspect.set(s.get("inspect", "fast"))
            self.var_maxpackshots.set(str(s.get("max_packshots", "8")))
            self.var_herovars.set(str(s.get("hero_variations", "4")))
            self.var_reuse.set(bool(s.get("reuse", True)))
            self.var_token.set(s.get("token", ""))
            self.var_model.set(s.get("model", "Nano Banana 2 (Gemini 3.1 Flash Image)"))
        except Exception:
            pass

    def _save_settings(self):
        try:
            import json as _json
            data = {
                "project": self.var_project.get(),
                "frames": self.var_frames.get(),
                "director_frames": self.var_director_frames.get(),
                "phases": self.var_phases.get(),
                "voice": self.var_voice.get(),
                "compose": self.var_compose.get(),
                "scene_type": self.var_scene_type.get(),
                "inspect": self.var_inspect.get(),
                "max_packshots": self.var_maxpackshots.get(),
                "hero_variations": self.var_herovars.get(),
                "reuse": self.var_reuse.get(),
                "token": self.var_token.get(),
                "model": self.var_model.get(),
            }
            with open(self._settings_path(), "w", encoding="utf-8") as fh:
                _json.dump(data, fh, indent=2)
        except Exception:
            pass

    def _on_close(self):
        self._save_settings()
        self.destroy()

    # ── pipeline runner (in-process, live log) ───────────────────────────
    def _build_argv(self):
        project = self.var_project.get().strip()
        frames = self.var_frames.get().strip()
        if not project:
            messagebox.showerror("Missing folder", "Please set a Project folder.")
            return None
        if not frames:
            messagebox.showerror("Missing folder", "Please set a Screenshots folder.")
            return None
        argv = ["archviz_director",
                "--project", project, "--frames", frames, "--recipes", RECIPES_DIR,
                "--phases", self.var_phases.get().strip() or "1,2,3,4,5",
                "--kokoro-voice", self.var_voice.get().strip() or "af_heart",
                "--compose-mode", self.var_compose.get().strip(),
                "--scene-type", self.var_scene_type.get().strip() or "exterior",
                "--inspect-mode", self.var_inspect.get().strip() or "fast",
                "--max-packshots", (self.var_maxpackshots.get().strip() or "8"),
                "--hero-variations", (self.var_herovars.get().strip() or "4")]
        if not self.var_reuse.get():
            argv.append("--no-reuse")
        if self.var_model.get().strip():
            argv += ["--image-model", self.var_model.get().strip()]
        if self.var_token.get().strip():
            argv += ["--api-token", self.var_token.get().strip()]
        df = self.var_director_frames.get().strip()
        if df:
            argv += ["--director-frames", df]
        if self.var_dryrun.get():
            argv.append("--dry-run")
        elif not comfyui_is_running():
            if not messagebox.askyesno("ComfyUI offline",
                                       "ComfyUI is not running. Run in dry-run mode instead?"):
                return None
            argv.append("--dry-run")
        return argv

    def _run_pipeline(self):
        if self._running:
            messagebox.showinfo("Already running", "Pipeline is already running.")
            return
        argv = self._build_argv()
        if argv is None:
            return
        self._save_settings()
        self._log("[info] Starting pipeline\u2026\n", "info")
        self._log(" ".join(f'"{a}"' if " " in a else a for a in argv) + "\n", "info")
        self._log("\u2500" * 60 + "\n")
        self.btn_run.config(state="disabled")
        self._running = True
        self._stop_flag = False
        threading.Thread(target=self._run_in_process, args=(argv,), daemon=True).start()

    def _run_in_process(self, argv):
        import io
        mod = load_pipeline()

        class LogRedirect(io.TextIOBase):
            def __init__(self, cb):
                self._cb = cb
            def write(self, s):
                if s:
                    self._cb(s)
                return len(s)
            def flush(self):
                pass

        def emit(s):
            tag = ("err" if any(w in s.lower() for w in ("error", "traceback", "failed"))
                   else "ok" if any(w in s for w in ("DONE", "done", "PASS"))
                   else "")
            self.after(0, self._log, s, tag)

        old_argv, old_out, old_err = sys.argv, sys.stdout, sys.stderr
        sys.argv = argv
        sys.stdout = sys.stderr = LogRedirect(emit)
        try:
            mod.main()
            self.after(0, self._log, "\n" + "\u2500" * 60 + "\nPipeline complete.\n", "ok")
        except SystemExit as e:
            code = e.code
            rule = "\u2500" * 60
            self.after(0, self._log, f"\n{rule}\nPipeline exited (code {code})\n",
                       "ok" if code in (0, None) else "err")
        except Exception:
            import traceback
            self.after(0, self._log, traceback.format_exc(), "err")
        finally:
            sys.argv, sys.stdout, sys.stderr = old_argv, old_out, old_err
            self._running = False
            self.after(0, lambda: self.btn_run.config(state="normal"))
            self.after(0, self.refresh_gallery)

    def _stop_pipeline(self):
        if self._running:
            self._stop_flag = True
            self._log("[info] Stop requested \u2014 current ComfyUI job will finish first.\n",
                      "info")

    # ── logging ──────────────────────────────────────────────────────────
    def _log(self, text, tag=""):
        self.log.insert("end", text, tag)
        self.log.see("end")


if __name__ == "__main__":
    App().mainloop()
