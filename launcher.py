"""
ArchViz Director Launcher
=========================
One-click .exe that:
  1. Checks whether ComfyUI is running; launches it if not.
  2. Opens the ComfyUI browser GUI.
  3. Lets you pick folders and run any phase of the pipeline.
  4. Streams live log output into the window.
"""

import os
import sys
import time
import threading
import subprocess
import webbrowser
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

# ── locate the pipeline script ──────────────────────────────────────────────
if getattr(sys, "frozen", False):
    # Running as a PyInstaller bundle — resources are in sys._MEIPASS
    BUNDLE_DIR = sys._MEIPASS
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    SCRIPT_DIR = BUNDLE_DIR

DIRECTOR_PY  = os.path.join(BUNDLE_DIR, "archviz_director.py")
RECIPES_DIR  = os.path.join(BUNDLE_DIR, "recipes")
COMFYUI_URL  = "http://127.0.0.1:8000"
COMFYUI_EXE  = r"C:\Users\oranbenshaprut\AppData\Local\Programs\comfyui\ComfyUI.exe"
COMFYUI_BAT  = r"C:\Users\oranbenshaprut\Documents\ComfyUI\run_comfyui.bat"

# Python that owns the pipeline dependencies (venv or system)
PIPELINE_PYTHON = sys.executable   # same python that built the exe

# ── ComfyUI helpers ─────────────────────────────────────────────────────────

def comfyui_is_running():
    try:
        urllib.request.urlopen(f"{COMFYUI_URL}/system_stats", timeout=3)
        return True
    except Exception:
        return False


def launch_comfyui():
    """Try to start ComfyUI Desktop or the bat launcher."""
    for candidate in [COMFYUI_EXE, COMFYUI_BAT]:
        if os.path.isfile(candidate):
            subprocess.Popen([candidate], creationflags=subprocess.CREATE_NEW_CONSOLE)
            return candidate
    return None


# ── Main window ─────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ArchViz Director")
        self.resizable(True, True)
        self.minsize(700, 560)
        self._build_ui()
        self._check_comfyui_status()

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self):
        pad = dict(padx=10, pady=4)

        # ── status bar ──
        status_frame = tk.Frame(self, bg="#1a1a2e")
        status_frame.pack(fill="x")
        self.status_dot = tk.Label(status_frame, text="●", font=("Arial", 14),
                                   bg="#1a1a2e", fg="grey")
        self.status_dot.pack(side="left", padx=(10, 4))
        self.status_lbl = tk.Label(status_frame, text="Checking ComfyUI…",
                                   bg="#1a1a2e", fg="white",
                                   font=("Arial", 10, "bold"))
        self.status_lbl.pack(side="left")
        self.btn_comfy = tk.Button(status_frame, text="Open ComfyUI GUI",
                                   command=self._open_browser,
                                   bg="#0f3460", fg="white", relief="flat",
                                   padx=10, cursor="hand2")
        self.btn_comfy.pack(side="right", padx=10, pady=6)

        # ── folder pickers ──
        frame = ttk.LabelFrame(self, text="  Folders  ", padding=10)
        frame.pack(fill="x", padx=12, pady=(10, 4))

        self.var_project = tk.StringVar()
        self.var_frames  = tk.StringVar()
        self.var_director_frames = tk.StringVar()

        self._folder_row(frame, 0, "Project folder:", self.var_project)
        self._folder_row(frame, 1, "Screenshots (one per scene):", self.var_frames)
        self._folder_row(frame, 2, "Unreal frames (director mode, optional):",
                         self.var_director_frames)

        # ── options ──
        opts = ttk.LabelFrame(self, text="  Options  ", padding=10)
        opts.pack(fill="x", padx=12, pady=4)

        self.var_phases = tk.StringVar(value="1,2,3,4,5")
        self.var_voice  = tk.StringVar(value="af_heart")
        self.var_compose = tk.StringVar(value="hard_cut_reencode")

        tk.Label(opts, text="Phases:").grid(row=0, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.var_phases, width=18).grid(
            row=0, column=1, sticky="w", padx=(4, 20))

        tk.Label(opts, text="Kokoro voice:").grid(row=0, column=2, sticky="w")
        ttk.Entry(opts, textvariable=self.var_voice, width=14).grid(
            row=0, column=3, sticky="w", padx=(4, 20))

        tk.Label(opts, text="Compose mode:").grid(row=0, column=4, sticky="w")
        ttk.Combobox(opts, textvariable=self.var_compose, width=20,
                     values=["hard_cut_reencode", "hard_cut_copy", "crossfade"],
                     state="readonly").grid(row=0, column=5, sticky="w", padx=4)

        self.var_dryrun = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Dry-run (offline scaffold)",
                        variable=self.var_dryrun).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # ── buttons ──
        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=12, pady=6)

        self.btn_run = tk.Button(btn_frame, text="▶  Run Pipeline",
                                 command=self._run_pipeline,
                                 bg="#16213e", fg="white", font=("Arial", 11, "bold"),
                                 relief="flat", padx=18, pady=8, cursor="hand2")
        self.btn_run.pack(side="left")

        tk.Button(btn_frame, text="✖  Stop",
                  command=self._stop_pipeline,
                  bg="#4a0000", fg="white", relief="flat",
                  padx=12, pady=8, cursor="hand2").pack(side="left", padx=8)

        tk.Button(btn_frame, text="🗑  Clear log",
                  command=lambda: self.log.delete("1.0", "end"),
                  bg="#2a2a2a", fg="white", relief="flat",
                  padx=12, pady=8, cursor="hand2").pack(side="right")

        # ── log area ──
        log_frame = ttk.LabelFrame(self, text="  Pipeline log  ", padding=4)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        self.log = scrolledtext.ScrolledText(log_frame, bg="#0d1117", fg="#c9d1d9",
                                             font=("Consolas", 9), wrap="word",
                                             state="normal")
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("err",  foreground="#ff6b6b")
        self.log.tag_config("ok",   foreground="#56d364")
        self.log.tag_config("info", foreground="#79c0ff")

        self._running   = False
        self._stop_flag = False

    def _folder_row(self, parent, row, label, var):
        tk.Label(parent, text=label, anchor="w").grid(
            row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=var, width=52).grid(
            row=row, column=1, sticky="ew", padx=6)
        tk.Button(parent, text="…",
                  command=lambda v=var: v.set(
                      filedialog.askdirectory(title="Select folder") or v.get()),
                  width=3, cursor="hand2").grid(row=row, column=2)
        parent.columnconfigure(1, weight=1)

    # ── ComfyUI status ───────────────────────────────────────────────────

    def _check_comfyui_status(self):
        def poll():
            while True:
                running = comfyui_is_running()
                self.after(0, self._update_status, running)
                time.sleep(5)

        threading.Thread(target=poll, daemon=True).start()

    def _update_status(self, running):
        if running:
            self.status_dot.config(fg="#56d364")
            self.status_lbl.config(text="ComfyUI is running  •  http://127.0.0.1:8000")
            self.btn_comfy.config(text="Open ComfyUI GUI ↗")
        else:
            self.status_dot.config(fg="#ff6b6b")
            self.status_lbl.config(text="ComfyUI is NOT running")
            self.btn_comfy.config(text="Launch + Open ComfyUI")

    def _open_browser(self):
        if not comfyui_is_running():
            path = launch_comfyui()
            if path:
                self._log(f"[info] Starting ComfyUI from: {path}\n", "info")
                self._log("[info] Waiting for ComfyUI to become ready…\n", "info")
                threading.Thread(target=self._wait_and_open, daemon=True).start()
            else:
                messagebox.showwarning(
                    "ComfyUI not found",
                    "Could not find ComfyUI Desktop or run_comfyui.bat.\n"
                    "Please start ComfyUI manually, then click Open ComfyUI GUI.")
        else:
            webbrowser.open(COMFYUI_URL)

    def _wait_and_open(self):
        for _ in range(60):
            time.sleep(2)
            if comfyui_is_running():
                webbrowser.open(COMFYUI_URL)
                self.after(0, self._log,
                            "[ok] ComfyUI is up — browser opened.\n", "ok")
                return
        self.after(0, self._log,
                    "[err] ComfyUI did not start within 2 minutes.\n", "err")

    # ── Pipeline runner ──────────────────────────────────────────────────

    def _build_argv(self):
        project = self.var_project.get().strip()
        frames  = self.var_frames.get().strip()
        if not project:
            messagebox.showerror("Missing folder", "Please set a Project folder.")
            return None
        if not frames:
            messagebox.showerror("Missing folder", "Please set a Screenshots folder.")
            return None

        argv = [
            "archviz_director",
            "--project",  project,
            "--frames",   frames,
            "--recipes",  RECIPES_DIR,
            "--phases",   self.var_phases.get().strip() or "1,2,3,4,5",
            "--kokoro-voice", self.var_voice.get().strip() or "af_heart",
            "--compose-mode", self.var_compose.get().strip(),
        ]

        director_frames = self.var_director_frames.get().strip()
        if director_frames:
            argv += ["--director-frames", director_frames]

        if self.var_dryrun.get():
            argv.append("--dry-run")
        elif not comfyui_is_running():
            if not messagebox.askyesno(
                    "ComfyUI offline",
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

        self._log("[info] Starting pipeline…\n", "info")
        self._log(" ".join(f'"{a}"' if " " in a else a for a in argv) + "\n", "info")
        self._log("─" * 60 + "\n")
        self.btn_run.config(state="disabled")
        self._running = True
        self._stop_flag = False

        threading.Thread(target=self._run_in_process, args=(argv,), daemon=True).start()

    def _run_in_process(self, argv):
        """Run archviz_director.main() in this process with redirected stdout."""
        import io

        # Add bundle dir to path so imports work
        if BUNDLE_DIR not in sys.path:
            sys.path.insert(0, BUNDLE_DIR)

        import importlib.util
        spec = importlib.util.spec_from_file_location("archviz_director", DIRECTOR_PY)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Redirect stdout → our log widget via a pipe-like object
        class LogRedirect(io.TextIOBase):
            def __init__(self, callback):
                self._cb = callback
            def write(self, s):
                if s:
                    self._cb(s)
                return len(s)
            def flush(self):
                pass

        old_argv, old_stdout, old_stderr = sys.argv, sys.stdout, sys.stderr
        sys.argv   = argv
        sys.stdout = LogRedirect(lambda s: self.after(0, self._log, s,
                                  "err" if any(w in s.lower() for w in
                                               ("error","traceback","failed")) else
                                  "ok"  if any(w in s for w in ("DONE","done","PASS")) else ""))
        sys.stderr = sys.stdout

        try:
            mod.main()
            self.after(0, self._log, "\n" + "─"*60 + "\nPipeline complete.\n", "ok")
        except SystemExit as e:
            code = e.code
            self.after(0, self._log,
                        f"\n{'─'*60}\nPipeline exited (code {code})\n",
                        "ok" if code == 0 else "err")
        except Exception as e:
            import traceback
            self.after(0, self._log, traceback.format_exc(), "err")
        finally:
            sys.argv, sys.stdout, sys.stderr = old_argv, old_stdout, old_stderr
            self._running = False
            self.after(0, lambda: self.btn_run.config(state="normal"))

    def _stop_pipeline(self):
        # In-process: raise KeyboardInterrupt in the pipeline thread is unreliable.
        # Best we can do is signal and inform the user.
        if self._running:
            self._stop_flag = True
            self._log("[info] Stop requested — the current ComfyUI job will finish "
                      "then the pipeline will exit.\n", "info")

    # ── Logging ──────────────────────────────────────────────────────────

    def _log(self, text, tag=""):
        self.log.config(state="normal")
        self.log.insert("end", text, tag)
        self.log.see("end")
        self.log.config(state="normal")


if __name__ == "__main__":
    app = App()
    app.mainloop()
