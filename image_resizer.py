"""
resize_gui.py — GUI for resizing JPEG images to web and Instagram sizes.

Run with:  python resize_gui.py
"""

import json
import threading
from pathlib import Path
from tkinter import (
    Tk, Toplevel, Frame, Label, Button, Entry, StringVar, Text, Scrollbar,
    Canvas, filedialog, messagebox, END, DISABLED, NORMAL, RIGHT, Y, BOTH, X, LEFT, W
)
from tkinter import ttk

from PIL import Image, ImageOps, ImageTk

WEB_LONG_EDGE = 2000
INSTA_CROP_SIZE = (1080, 1080)
JPEG_SUFFIXES = {".jpg", ".jpeg"}

# Max size of the preview canvas in the crop dialog
PREVIEW_MAX = 700

# 18% grey colour palette
BG       = "#484848"   # window / frame background (~18% grey)
BG_DARK  = "#383838"   # entry fields, log, canvas surround
FG       = "#d8d8d8"   # primary text
FG_DIM   = "#a0a0a0"   # secondary / status text
BTN_BG   = "#585858"   # neutral button background
BTN_ACT  = "#686868"   # neutral button hover/active
WEB_BTN  = "#2a6099"   # blue action button (muted)
WEB_ACT  = "#1e4d7a"
INST_BTN = "#8c2a20"   # red action button (muted)
INST_ACT = "#6b1f18"

CROPS_FILENAME = "crop_settings.json"


# ── Crop settings persistence ──────────────────────────────────────────────────

def load_crops(insta_dir: Path) -> dict:
    """Load saved crop boxes from insta_dir/crop_settings.json. Returns {filename: [l,t,r,b] or None}."""
    path = insta_dir / CROPS_FILENAME
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_crops(insta_dir: Path, crops: dict) -> None:
    """Persist crop boxes to insta_dir/crop_settings.json."""
    insta_dir.mkdir(parents=True, exist_ok=True)
    serialisable = {
        k.name if isinstance(k, Path) else k: list(v) if v is not None else None
        for k, v in crops.items()
        if v != "skip"
    }
    path = insta_dir / CROPS_FILENAME
    path.write_text(json.dumps(serialisable, indent=2), encoding="utf-8")


# ── Core resize logic ──────────────────────────────────────────────────────────

def get_jpeg_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(f for f in path.iterdir() if f.is_file() and f.suffix.lower() in JPEG_SUFFIXES)
    elif path.is_file() and path.suffix.lower() in JPEG_SUFFIXES:
        return [path]
    return []


def resize_web(img: Image.Image, long_edge: int = WEB_LONG_EDGE) -> Image.Image:
    w, h = img.size
    scale = long_edge / max(w, h)
    if scale >= 1.0:
        return img.copy()
    return img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)


def resize_insta_center(img: Image.Image, crop_size: tuple = INSTA_CROP_SIZE) -> Image.Image:
    """Auto center crop to square."""
    return ImageOps.fit(img, crop_size, Image.LANCZOS)


def resize_insta_custom(img: Image.Image, crop_box: tuple, insta_px: int = 1080) -> Image.Image:
    """Crop using a box in the intermediate (long-edge=insta_px) image coordinates, return square."""
    w, h = img.size
    scale = insta_px / max(w, h)
    scaled = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    return scaled.crop(crop_box)


def save_image(img: Image.Image, out_path: Path, exif_bytes: bytes) -> None:
    kwargs: dict = {"quality": 100, "subsampling": 0}
    if exif_bytes:
        kwargs["exif"] = exif_bytes
    img.save(out_path, "JPEG", **kwargs)


def process_web(filepath: Path, web_dir: Path, web_px: int = WEB_LONG_EDGE) -> str:
    """Returns 'done' or 'skipped'."""
    web_out = web_dir / f"{filepath.stem}_Web.jpg"
    if web_out.exists():
        return "skipped"
    with Image.open(filepath) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        exif_bytes = img.info.get("exif", b"")
        web_dir.mkdir(parents=True, exist_ok=True)
        save_image(resize_web(img, web_px), web_out, exif_bytes)
    return "done"


def process_insta(filepath: Path, insta_dir: Path, crop_box=None, insta_px: int = 1080) -> str:
    """Returns 'done' or 'skipped'. crop_box is in insta_px-scaled image coords, or None for center crop."""
    insta_out = insta_dir / f"{filepath.stem}_Insta.jpg"
    if insta_out.exists():
        return "skipped"
    crop_size = (insta_px, insta_px)
    with Image.open(filepath) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        exif_bytes = img.info.get("exif", b"")
        insta_dir.mkdir(parents=True, exist_ok=True)
        if crop_box is not None:
            insta_img = resize_insta_custom(img, crop_box, insta_px)
        else:
            insta_img = resize_insta_center(img, crop_size)
        save_image(insta_img, insta_out, exif_bytes)
    return "done"


# ── Crop Dialog ────────────────────────────────────────────────────────────────

class CropDialog(Toplevel):
    """
    Shows a preview of the image (scaled to long edge = 1080px, then fitted in the window).
    A square crop box can be dragged around. Returns the crop box in 1080-scaled image coords.
    Result is stored in self.result: tuple (left, top, right, bottom) or None if center-crop chosen.
    """

    def __init__(self, parent, filepath: Path, index: int, total: int, initial_crop=None):
        super().__init__(parent)
        self.title(f"Crop for Instagram  —  {filepath.name}  ({index}/{total})")
        self.resizable(False, False)
        self.grab_set()  # modal

        self.result = None  # None = use center crop
        self._cancelled = False
        self._cancel_all = False
        self.protocol("WM_DELETE_WINDOW", self._cancel_all_fn)

        # Load and scale image to long edge = 1080
        with Image.open(filepath) as raw:
            raw = ImageOps.exif_transpose(raw)
            if raw.mode != "RGB":
                raw = raw.convert("RGB")
            w, h = raw.size
            scale = 1080 / max(w, h)
            self._img_1080 = raw.resize((round(w * scale), round(h * scale)), Image.LANCZOS)

        iw, ih = self._img_1080.size

        # Scale the 1080-image down further to fit in PREVIEW_MAX
        self._preview_scale = min(PREVIEW_MAX / iw, PREVIEW_MAX / ih, 1.0)
        pw = round(iw * self._preview_scale)
        ph = round(ih * self._preview_scale)
        self._preview_w = pw
        self._preview_h = ph

        # Box size in preview pixels (1080 * preview_scale)
        self._box_px = round(1080 * self._preview_scale)
        # Clamp box so it fits within the image preview
        self._box_px = min(self._box_px, pw, ph)

        # Initial box position: from saved crop or centered
        s = self._preview_scale
        if initial_crop is not None:
            left, top, right, bottom = initial_crop
            self._bx = max(0, min(round(left * s), pw - self._box_px))
            self._by = max(0, min(round(top * s), ph - self._box_px))
        else:
            self._bx = (pw - self._box_px) // 2
            self._by = (ph - self._box_px) // 2
        self._drag_start = None

        self._has_saved = initial_crop is not None
        self._build_ui(pw, ph, filepath)
        self._draw()
        self._center_window()

    def _build_ui(self, pw, ph, filepath):
        self.config(bg=BG)
        hint = "Showing your saved crop — drag to adjust." if self._has_saved else "Drag the box to set your Instagram crop area."
        Label(self, text=hint, font=("Segoe UI", 9), pady=6,
              bg=BG, fg=FG).pack()

        self._canvas = Canvas(self, width=pw, height=ph, cursor="fleur",
                              highlightthickness=1, highlightbackground=BG_DARK,
                              bg=BG_DARK)
        self._canvas.pack(padx=12)

        # Render preview image onto canvas
        preview_img = self._img_1080.resize((pw, ph), Image.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(preview_img)
        self._canvas.create_image(0, 0, anchor="nw", image=self._tk_img)

        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)

        btn_frame = Frame(self, pady=10, bg=BG)
        btn_frame.pack()

        Button(btn_frame, text="Use This Crop", command=self._use_crop,
               font=("Segoe UI", 10, "bold"), width=16, height=2,
               bg=INST_BTN, fg=FG, activebackground=INST_ACT,
               activeforeground=FG, relief="flat", cursor="hand2").pack(side=LEFT, padx=6)

        Button(btn_frame, text="Use Center Crop", command=self._use_center,
               font=("Segoe UI", 10), width=16, height=2,
               bg=BTN_BG, fg=FG, activebackground=BTN_ACT,
               activeforeground=FG, relief="flat", cursor="hand2").pack(side=LEFT, padx=6)

        Button(btn_frame, text="Skip This Image", command=self._skip,
               font=("Segoe UI", 10), width=14, height=2,
               bg=BTN_BG, fg=FG, activebackground=BTN_ACT,
               activeforeground=FG, relief="flat", cursor="hand2").pack(side=LEFT, padx=6)

        Button(btn_frame, text="Cancel All", command=self._cancel_all_fn,
               font=("Segoe UI", 10), width=10, height=2,
               bg=BG_DARK, fg=FG_DIM, activebackground=BTN_BG,
               activeforeground=FG, relief="flat", cursor="hand2").pack(side=LEFT, padx=6)

    def _center_window(self):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _draw(self):
        self._canvas.delete("cropbox")
        x, y = self._bx, self._by
        s = self._box_px
        # Dark overlay outside box — draw 4 rectangles
        pw, ph = self._preview_w, self._preview_h
        self._canvas.create_rectangle(0, 0, pw, y, fill="black", stipple="gray50", outline="", tags="cropbox")
        self._canvas.create_rectangle(0, y + s, pw, ph, fill="black", stipple="gray50", outline="", tags="cropbox")
        self._canvas.create_rectangle(0, y, x, y + s, fill="black", stipple="gray50", outline="", tags="cropbox")
        self._canvas.create_rectangle(x + s, y, pw, y + s, fill="black", stipple="gray50", outline="", tags="cropbox")
        # Crop box border
        self._canvas.create_rectangle(x, y, x + s, y + s, outline="white", width=2, tags="cropbox")
        # Rule-of-thirds lines inside box
        t = s // 3
        for i in (1, 2):
            self._canvas.create_line(x + t * i, y, x + t * i, y + s, fill="white", width=1, tags="cropbox")
            self._canvas.create_line(x, y + t * i, x + s, y + t * i, fill="white", width=1, tags="cropbox")

    def _on_press(self, event):
        self._drag_start = (event.x - self._bx, event.y - self._by)

    def _on_drag(self, event):
        if self._drag_start is None:
            return
        ox, oy = self._drag_start
        new_x = event.x - ox
        new_y = event.y - oy
        # Clamp within preview
        new_x = max(0, min(new_x, self._preview_w - self._box_px))
        new_y = max(0, min(new_y, self._preview_h - self._box_px))
        self._bx, self._by = new_x, new_y
        self._draw()

    def _use_crop(self):
        # Convert preview coords back to 1080-image coords
        s = self._preview_scale
        left = round(self._bx / s)
        top = round(self._by / s)
        size = round(self._box_px / s)
        self.result = (left, top, left + size, top + size)
        self.destroy()

    def _use_center(self):
        self.result = None  # signals center crop
        self.destroy()

    def _skip(self):
        self._cancelled = True
        self.result = None
        self.destroy()

    def _cancel_all_fn(self):
        self._cancelled = True
        self._cancel_all = True
        self.result = None
        self.destroy()


# ── Main App ───────────────────────────────────────────────────────────────────

class App(Tk):
    def __init__(self):
        super().__init__()
        self.title("Image Resizer")
        self.resizable(False, False)
        self._build_ui()
        self._center_window()

    def _center_window(self):
        self.update_idletasks()
        w, h = 600, 480
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    def _build_ui(self):
        self.config(bg=BG)
        pad = {"padx": 12, "pady": 6}

        # ── Input ──────────────────────────────────────────────────────────────
        input_frame = Frame(self, bg=BG)
        input_frame.pack(fill=X, **pad)

        Label(input_frame, text="Input", font=("Segoe UI", 9, "bold"), anchor=W,
              bg=BG, fg=FG).pack(fill=X)

        row = Frame(input_frame, bg=BG)
        row.pack(fill=X, pady=(2, 0))
        self.input_var = StringVar()
        Entry(row, textvariable=self.input_var, width=60,
              bg=BG_DARK, fg=FG, insertbackground=FG,
              relief="flat", bd=4).pack(side=LEFT, expand=True, fill=X)
        Button(row, text="Browse Folder", command=self._browse_input_folder, width=14,
               bg=BTN_BG, fg=FG, activebackground=BTN_ACT, activeforeground=FG,
               relief="flat", cursor="hand2").pack(side=LEFT, padx=(6, 0))
        Button(row, text="Browse File", command=self._browse_input_file, width=12,
               bg=BTN_BG, fg=FG, activebackground=BTN_ACT, activeforeground=FG,
               relief="flat", cursor="hand2").pack(side=LEFT, padx=(4, 0))

        # ── Output ────────────────────────────────────────────────────────────
        output_frame = Frame(self, bg=BG)
        output_frame.pack(fill=X, **pad)

        Label(output_frame, text="Output folder  (Web and Insta subfolders will be created here)",
              font=("Segoe UI", 9, "bold"), anchor=W, bg=BG, fg=FG).pack(fill=X)

        row2 = Frame(output_frame, bg=BG)
        row2.pack(fill=X, pady=(2, 0))
        self.output_var = StringVar()
        Entry(row2, textvariable=self.output_var, width=60,
              bg=BG_DARK, fg=FG, insertbackground=FG,
              relief="flat", bd=4).pack(side=LEFT, expand=True, fill=X)
        Button(row2, text="Browse Folder", command=self._browse_output_folder, width=14,
               bg=BTN_BG, fg=FG, activebackground=BTN_ACT, activeforeground=FG,
               relief="flat", cursor="hand2").pack(side=LEFT, padx=(6, 0))

        # ── Size settings ─────────────────────────────────────────────────────
        size_frame = Frame(self, bg=BG)
        size_frame.pack(fill=X, padx=12, pady=(2, 4))

        Label(size_frame, text="Web long edge (px):", bg=BG, fg=FG,
              font=("Segoe UI", 9)).pack(side=LEFT)
        self.web_size_var = StringVar(value="2000")
        Entry(size_frame, textvariable=self.web_size_var, width=6,
              bg=BG_DARK, fg=FG, insertbackground=FG, relief="flat", bd=4,
              justify="center").pack(side=LEFT, padx=(4, 20))

        Label(size_frame, text="Instagram size (px):", bg=BG, fg=FG,
              font=("Segoe UI", 9)).pack(side=LEFT)
        self.insta_size_var = StringVar(value="1080")
        Entry(size_frame, textvariable=self.insta_size_var, width=6,
              bg=BG_DARK, fg=FG, insertbackground=FG, relief="flat", bd=4,
              justify="center").pack(side=LEFT, padx=(4, 0))

        Label(size_frame, text="(square)", bg=BG, fg=FG_DIM,
              font=("Segoe UI", 8)).pack(side=LEFT, padx=(4, 0))

        # ── Progress ──────────────────────────────────────────────────────────
        prog_frame = Frame(self, bg=BG)
        prog_frame.pack(fill=X, padx=12, pady=(6, 2))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Grey.Horizontal.TProgressbar",
                        troughcolor=BG_DARK, background=WEB_BTN, bordercolor=BG, lightcolor=WEB_BTN, darkcolor=WEB_BTN)
        self.progress_bar = ttk.Progressbar(prog_frame, mode="determinate", style="Grey.Horizontal.TProgressbar")
        self.progress_bar.pack(fill=X)

        self.status_label = Label(prog_frame, text="Ready", anchor=W,
                                  fg=FG_DIM, bg=BG, font=("Segoe UI", 8))
        self.status_label.pack(fill=X, pady=(2, 0))

        # ── Log ───────────────────────────────────────────────────────────────
        log_frame = Frame(self, bg=BG)
        log_frame.pack(fill=BOTH, expand=True, padx=12, pady=(2, 6))

        scrollbar = Scrollbar(log_frame, bg=BTN_BG, troughcolor=BG_DARK,
                              activebackground=BTN_ACT, relief="flat")
        scrollbar.pack(side=RIGHT, fill=Y)

        self.log = Text(log_frame, height=12, state=DISABLED, font=("Consolas", 8),
                        yscrollcommand=scrollbar.set, bg=BG_DARK, fg=FG,
                        insertbackground=FG, relief="flat", bd=1,
                        selectbackground=BTN_BG, selectforeground=FG)
        self.log.pack(fill=BOTH, expand=True)
        scrollbar.config(command=self.log.yview)

        # ── Action buttons ────────────────────────────────────────────────────
        btn_frame = Frame(self, bg=BG)
        btn_frame.pack(pady=(0, 12))

        self.web_btn = Button(btn_frame, text="Resize for Web", command=self._start_web,
                              font=("Segoe UI", 10, "bold"), width=18, height=2,
                              bg=WEB_BTN, fg=FG, activebackground=WEB_ACT,
                              activeforeground=FG, relief="flat", cursor="hand2")
        self.web_btn.pack(side=LEFT, padx=6)

        self.insta_btn = Button(btn_frame, text="Crop for Instagram", command=self._start_insta,
                                font=("Segoe UI", 10, "bold"), width=18, height=2,
                                bg=INST_BTN, fg=FG, activebackground=INST_ACT,
                                activeforeground=FG, relief="flat", cursor="hand2")
        self.insta_btn.pack(side=LEFT, padx=6)

    # ── Browse helpers ────────────────────────────────────────────────────────

    def _browse_input_folder(self):
        path = filedialog.askdirectory(title="Select input folder")
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(path)

    def _browse_input_file(self):
        path = filedialog.askopenfilename(
            title="Select JPEG file",
            filetypes=[("JPEG files", "*.jpg *.jpeg"), ("All files", "*.*")]
        )
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(str(Path(path).parent))

    def _browse_output_folder(self):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.output_var.set(path)

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log.config(state=NORMAL)
        self.log.insert(END, msg + "\n")
        self.log.see(END)
        self.log.config(state=DISABLED)

    # ── Shared helpers ─────────────────────────────────────────────────────────

    def _get_sizes(self):
        """Read and validate web/insta size inputs. Returns (web_px, insta_px) or (None, None)."""
        try:
            web_px = int(self.web_size_var.get().strip())
            assert 100 <= web_px <= 20000
        except (ValueError, AssertionError):
            messagebox.showwarning("Invalid size", "Web size must be a whole number between 100 and 20000.")
            return None, None
        try:
            insta_px = int(self.insta_size_var.get().strip())
            assert 100 <= insta_px <= 20000
        except (ValueError, AssertionError):
            messagebox.showwarning("Invalid size", "Instagram size must be a whole number between 100 and 20000.")
            return None, None
        return web_px, insta_px

    def _get_files_and_output(self):
        """Validate inputs and return (files, out_root) or (None, None)."""
        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()
        if not input_path:
            messagebox.showwarning("No input", "Please select an input file or folder.")
            return None, None
        if not output_path:
            messagebox.showwarning("No output", "Please select an output folder.")
            return None, None
        files = get_jpeg_files(Path(input_path))
        if not files:
            messagebox.showerror("No JPEGs found", f"No JPEG files found in:\n{input_path}")
            return None, None
        return files, Path(output_path)

    def _lock_buttons(self):
        self.web_btn.config(state=DISABLED)
        self.insta_btn.config(state=DISABLED)

    def _unlock_buttons(self):
        self.web_btn.config(state=NORMAL)
        self.insta_btn.config(state=NORMAL)

    def _clear_log(self):
        self.log.config(state=NORMAL)
        self.log.delete("1.0", END)
        self.log.config(state=DISABLED)

    def _finish(self, errors, done, skipped, manually_skipped, out_root, mode):
        self._log("")
        if errors:
            self._log(f"Completed with {len(errors)} error(s).")
            self.status_label.config(text=f"Done — {len(errors)} error(s). See log.", fg="#c0504d")
        else:
            parts = []
            if done:
                parts.append(f"{done} done")
            if skipped:
                parts.append(f"{skipped} already existed")
            if manually_skipped:
                parts.append(f"{manually_skipped} skipped")
            summary = ", ".join(parts) + "."
            self._log(summary)
            folder = out_root / ("Web" if mode == "web" else "Insta")
            self._log(f"Output → {folder}")
            self.status_label.config(text=summary, fg="#7caa6e")
        self._unlock_buttons()

    # ── Web ────────────────────────────────────────────────────────────────────

    def _start_web(self):
        files, out_root = self._get_files_and_output()
        if files is None:
            return
        web_px, _ = self._get_sizes()
        if web_px is None:
            return
        self._lock_buttons()
        self._clear_log()
        web_dir = out_root / "Web"
        total = len(files)
        self.progress_bar["maximum"] = total
        self.progress_bar["value"] = 0
        self._log(f"Resizing {total} file(s) for Web at {web_px}px long edge...\n")
        threading.Thread(target=self._run_web, args=(files, web_dir, web_px), daemon=True).start()

    def _run_web(self, files, web_dir, web_px):
        total = len(files)
        errors, skipped, done = [], 0, 0
        for i, fp in enumerate(files, 1):
            self.status_label.config(text=f"Processing {i}/{total}: {fp.name}")
            try:
                result = process_web(fp, web_dir, web_px)
                if result == "skipped":
                    self._log(f"  –  {fp.name}  (already exists, skipped)")
                    skipped += 1
                else:
                    self._log(f"  ✓  {fp.name}")
                    done += 1
            except Exception as e:
                self._log(f"  ✗  {fp.name}  — {e}")
                errors.append((fp, e))
            self.progress_bar["value"] = i
        self._finish(errors, done, skipped, 0, web_dir.parent, "web")

    # ── Instagram ──────────────────────────────────────────────────────────────

    def _start_insta(self):
        files, out_root = self._get_files_and_output()
        if files is None:
            return
        _, insta_px = self._get_sizes()
        if insta_px is None:
            return
        self._lock_buttons()
        self._clear_log()
        insta_dir = out_root / "Insta"

        # Load any previously saved crop boxes
        saved = load_crops(insta_dir)  # {filename: [l,t,r,b] or None}

        # Filter to only files that need processing
        todo = [fp for fp in files if not (insta_dir / f"{fp.stem}_Insta.jpg").exists()]
        already_done = len(files) - len(todo)

        total = len(files)
        self.progress_bar["maximum"] = total
        self.progress_bar["value"] = already_done
        self._log(f"Cropping {len(todo)} file(s) for Instagram at {insta_px}x{insta_px}px...\n")

        # Show crop dialog for each pending file (must run on main thread)
        crops = {}
        for i, fp in enumerate(todo, 1):
            prev = saved.get(fp.name)  # list [l,t,r,b] or None
            prev_box = tuple(prev) if prev is not None else None
            dlg = CropDialog(self, fp, i, len(todo), initial_crop=prev_box)
            self.wait_window(dlg)
            if dlg._cancel_all:
                self._log("Cancelled — no images processed.")
                self.status_label.config(text="Cancelled.", fg=FG_DIM)
                self._unlock_buttons()
                return
            crops[fp] = ("skip" if dlg._cancelled else dlg.result)

        threading.Thread(
            target=self._run_insta,
            args=(files, insta_dir, crops, already_done, saved, insta_px),
            daemon=True
        ).start()

    def _run_insta(self, files, insta_dir, crops, already_done, saved, insta_px):
        total = len(files)
        errors, skipped, done, manually_skipped = [], already_done, 0, 0
        processed = already_done

        # Log the pre-skipped files
        for fp in files:
            if fp not in crops:
                self._log(f"  –  {fp.name}  (already exists, skipped)")

        for fp, crop in crops.items():
            processed += 1
            self.status_label.config(text=f"Processing {processed}/{total}: {fp.name}")
            if crop == "skip":
                self._log(f"  –  {fp.name}  (skipped by user)")
                manually_skipped += 1
                self.progress_bar["value"] = processed
                continue
            try:
                result = process_insta(fp, insta_dir, crop_box=crop, insta_px=insta_px)
                if result == "skipped":
                    self._log(f"  –  {fp.name}  (already exists, skipped)")
                    skipped += 1
                else:
                    label = "center crop" if crop is None else "custom crop"
                    self._log(f"  ✓  {fp.name}  ({label})")
                    done += 1
            except Exception as e:
                self._log(f"  ✗  {fp.name}  — {e}")
                errors.append((fp, e))
            self.progress_bar["value"] = processed

        # Merge new crops into saved and persist
        merged = dict(saved)
        for fp, crop in crops.items():
            if crop != "skip":
                merged[fp.name] = list(crop) if crop is not None else None
        save_crops(insta_dir, merged)

        self._finish(errors, done, skipped, manually_skipped, insta_dir.parent, "insta")


if __name__ == "__main__":
    app = App()
    app.mainloop()
