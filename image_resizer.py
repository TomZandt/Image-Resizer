"""
image_resizer.py — Resize JPEG images for web and Instagram.

Run with:  python image_resizer.py

Web output:       long edge resized to a configurable pixel width (default 2000px)
Instagram output: square center-crop at a configurable size (default 1080x1080px)

Both outputs are saved at 100% JPEG quality with EXIF data preserved.
Previously processed files are skipped automatically.
Instagram crop positions are saved and restored between sessions.
"""

import json
import threading
from pathlib import Path
from tkinter import (
    Tk, Toplevel, Frame, Label, Button, Entry, StringVar,
    Text, Scrollbar, Canvas,
    filedialog, messagebox,
    END, DISABLED, NORMAL, RIGHT, Y, BOTH, X, LEFT,
)
from tkinter import ttk

from PIL import Image, ImageOps, ImageTk


# ── Constants ──────────────────────────────────────────────────────────────────

JPEG_SUFFIXES  = {".jpg", ".jpeg"}
CROPS_FILENAME = "crop_settings.json"

# Default output sizes
DEFAULT_WEB_PX   = 2000
DEFAULT_INSTA_PX = 1080

# Maximum size of the image preview canvas in the crop dialog
PREVIEW_MAX = 700

# Colour palette — 18% grey, neutral for colour-accurate work
BG       = "#484848"   # window / frame background
BG_DARK  = "#383838"   # entry fields, log area, canvas border
FG       = "#d8d8d8"   # primary text
FG_DIM   = "#a0a0a0"   # secondary / status text
BTN_BG   = "#585858"   # neutral button
BTN_ACT  = "#686868"   # neutral button (hover)
WEB_BTN  = "#2a6099"   # web action button (blue)
WEB_ACT  = "#1e4d7a"
INST_BTN = "#8c2a20"   # instagram action button (red)
INST_ACT = "#6b1f18"


# ── Crop settings — load / save ────────────────────────────────────────────────

def load_crops(insta_dir: Path) -> dict:
    """
    Load previously saved crop boxes from crop_settings.json inside insta_dir.
    Returns a dict mapping filename -> [left, top, right, bottom] or None.
    Returns an empty dict if no file exists or it cannot be parsed.
    """
    path = insta_dir / CROPS_FILENAME
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_crops(insta_dir: Path, crops: dict) -> None:
    """
    Save crop boxes to crop_settings.json inside insta_dir.
    Skipped images (value == "skip") are not written to the file.
    """
    insta_dir.mkdir(parents=True, exist_ok=True)

    serialisable = {
        (k.name if isinstance(k, Path) else k): (list(v) if v is not None else None)
        for k, v in crops.items()
        if v != "skip"
    }

    path = insta_dir / CROPS_FILENAME
    path.write_text(json.dumps(serialisable, indent=2), encoding="utf-8")


# ── Image processing ───────────────────────────────────────────────────────────

def get_jpeg_files(path: Path) -> list[Path]:
    """Return a sorted list of JPEG files at path (file) or inside path (folder)."""
    if path.is_dir():
        return sorted(
            f for f in path.iterdir()
            if f.is_file() and f.suffix.lower() in JPEG_SUFFIXES
        )
    if path.is_file() and path.suffix.lower() in JPEG_SUFFIXES:
        return [path]
    return []


def open_image(filepath: Path) -> tuple[Image.Image, bytes]:
    """
    Open a JPEG, correct its orientation from EXIF, convert to RGB,
    and return the image alongside the raw EXIF bytes for later saving.
    """
    img = Image.open(filepath)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    exif_bytes = img.info.get("exif", b"")
    return img, exif_bytes


def save_jpeg(img: Image.Image, out_path: Path, exif_bytes: bytes) -> None:
    """Save img as a 100%-quality JPEG, preserving EXIF data if present."""
    kwargs: dict = {"quality": 100, "subsampling": 0}
    if exif_bytes:
        kwargs["exif"] = exif_bytes
    img.save(out_path, "JPEG", **kwargs)


def resize_for_web(img: Image.Image, long_edge: int) -> Image.Image:
    """
    Resize img so its longest side equals long_edge pixels.
    Aspect ratio is preserved. Images smaller than long_edge are not upscaled.
    """
    w, h = img.size
    scale = long_edge / max(w, h)
    if scale >= 1.0:
        return img.copy()
    return img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)


def crop_for_instagram_center(img: Image.Image, size: int) -> Image.Image:
    """Resize and center-crop img to a size x size square."""
    return ImageOps.fit(img, (size, size), Image.LANCZOS)


def crop_for_instagram_custom(img: Image.Image, crop_box: tuple, size: int) -> Image.Image:
    """
    Scale img so its longest side equals size pixels, then crop using crop_box.
    crop_box is (left, top, right, bottom) in the scaled image's coordinate space.
    """
    w, h = img.size
    scale = size / max(w, h)
    scaled = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    return scaled.crop(crop_box)


def process_web(filepath: Path, web_dir: Path, web_px: int) -> str:
    """
    Resize filepath for web and save to web_dir.
    Returns 'skipped' if the output already exists, otherwise 'done'.
    """
    out = web_dir / f"{filepath.stem}_Web.jpg"
    if out.exists():
        return "skipped"

    img, exif = open_image(filepath)
    web_dir.mkdir(parents=True, exist_ok=True)
    save_jpeg(resize_for_web(img, web_px), out, exif)

    return "done"


def process_insta(filepath: Path, insta_dir: Path, insta_px: int, crop_box=None) -> str:
    """
    Crop filepath for Instagram and save to insta_dir.
    crop_box is (left, top, right, bottom) in insta_px-scaled coordinates,
    or None to use an automatic center crop.
    Returns 'skipped' if the output already exists, otherwise 'done'.
    """
    out = insta_dir / f"{filepath.stem}_Insta.jpg"
    if out.exists():
        return "skipped"

    img, exif = open_image(filepath)
    insta_dir.mkdir(parents=True, exist_ok=True)

    if crop_box is not None:
        result = crop_for_instagram_custom(img, crop_box, insta_px)
    else:
        result = crop_for_instagram_center(img, insta_px)

    save_jpeg(result, out, exif)
    return "done"


# ── Crop dialog ────────────────────────────────────────────────────────────────

class CropDialog(Toplevel):
    """
    Modal dialog for positioning an Instagram crop on a single image.

    Shows a preview of the image with a draggable square crop box and
    rule-of-thirds grid lines. The user can confirm the crop, fall back to
    a center crop, skip this image, or cancel the entire queue.

    Attributes set after the dialog closes:
        result      — (left, top, right, bottom) in 1080px-scaled coords,
                      or None if center crop was chosen.
        cancelled   — True if this image was skipped.
        cancel_all  — True if the entire queue was cancelled.
    """

    def __init__(self, parent, filepath: Path, index: int, total: int, initial_crop=None):
        super().__init__(parent)
        self.title(f"Instagram Crop  —  {filepath.name}  ({index} of {total})")
        self.resizable(False, False)
        self.grab_set()
        self.config(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel_all)

        # Public result attributes
        self.result     = None
        self.cancelled  = False
        self.cancel_all = False

        # Load the image scaled so its longest side is 1080px
        with Image.open(filepath) as raw:
            raw = ImageOps.exif_transpose(raw)
            if raw.mode != "RGB":
                raw = raw.convert("RGB")
            w, h  = raw.size
            scale = 1080 / max(w, h)
            self._img_1080 = raw.resize((round(w * scale), round(h * scale)), Image.LANCZOS)

        iw, ih = self._img_1080.size

        # Scale down further to fit inside PREVIEW_MAX
        self._preview_scale = min(PREVIEW_MAX / iw, PREVIEW_MAX / ih, 1.0)
        pw = round(iw * self._preview_scale)
        ph = round(ih * self._preview_scale)
        self._preview_w = pw
        self._preview_h = ph

        # The crop box is a square representing 1080px in preview space
        self._box_px = min(round(1080 * self._preview_scale), pw, ph)

        # Position the crop box from saved data or center it
        s = self._preview_scale
        if initial_crop is not None:
            left, top, right, bottom = initial_crop
            self._bx = max(0, min(round(left * s), pw - self._box_px))
            self._by = max(0, min(round(top  * s), ph - self._box_px))
        else:
            self._bx = (pw - self._box_px) // 2
            self._by = (ph - self._box_px) // 2

        self._drag_start = None
        self._has_saved  = initial_crop is not None

        self._build_ui(pw, ph)
        self._draw_overlay()
        self._center_on_screen()

    def _build_ui(self, pw, ph):
        hint = (
            "Showing your saved crop — drag to adjust."
            if self._has_saved
            else "Drag the box to choose your crop area."
        )
        Label(self, text=hint, font=("Segoe UI", 9), pady=6, bg=BG, fg=FG).pack()

        self._canvas = Canvas(
            self, width=pw, height=ph, cursor="fleur",
            highlightthickness=1, highlightbackground=BG_DARK, bg=BG_DARK,
        )
        self._canvas.pack(padx=12)

        preview = self._img_1080.resize((pw, ph), Image.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(preview)
        self._canvas.create_image(0, 0, anchor="nw", image=self._tk_img)
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>",     self._on_drag)

        btn_frame = Frame(self, pady=10, bg=BG)
        btn_frame.pack()

        self._btn(btn_frame, "Use This Crop",  self._on_use_crop,   INST_BTN, INST_ACT, bold=True)
        self._btn(btn_frame, "Use Center Crop", self._on_use_center, BTN_BG,  BTN_ACT)
        self._btn(btn_frame, "Skip This Image", self._on_skip,       BTN_BG,  BTN_ACT)
        self._btn(btn_frame, "Cancel All",      self._on_cancel_all, BG_DARK, BTN_BG, fg=FG_DIM)

    def _btn(self, parent, text, command, bg, active_bg, fg=FG, bold=False):
        font = ("Segoe UI", 10, "bold") if bold else ("Segoe UI", 10)
        Button(
            parent, text=text, command=command,
            font=font, width=14, height=2,
            bg=bg, fg=fg, activebackground=active_bg, activeforeground=FG,
            relief="flat", cursor="hand2",
        ).pack(side=LEFT, padx=5)

    def _center_on_screen(self):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w,  h  = self.winfo_width(),       self.winfo_height()
        self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _draw_overlay(self):
        """Redraw the semi-transparent overlay and crop box with rule-of-thirds lines."""
        self._canvas.delete("overlay")
        x, y = self._bx, self._by
        s    = self._box_px
        pw   = self._preview_w
        ph   = self._preview_h

        # Darken the area outside the crop box
        for coords in [
            (0,     0,      pw, y      ),
            (0,     y + s,  pw, ph     ),
            (0,     y,      x,  y + s  ),
            (x + s, y,      pw, y + s  ),
        ]:
            self._canvas.create_rectangle(
                *coords, fill="black", stipple="gray50", outline="", tags="overlay"
            )

        # Crop box border
        self._canvas.create_rectangle(x, y, x + s, y + s, outline="white", width=2, tags="overlay")

        # Rule-of-thirds grid
        t = s // 3
        for i in (1, 2):
            self._canvas.create_line(x + t*i, y, x + t*i, y + s, fill="white", width=1, tags="overlay")
            self._canvas.create_line(x, y + t*i, x + s, y + t*i, fill="white", width=1, tags="overlay")

    def _on_press(self, event):
        self._drag_start = (event.x - self._bx, event.y - self._by)

    def _on_drag(self, event):
        if self._drag_start is None:
            return
        ox, oy    = self._drag_start
        self._bx  = max(0, min(event.x - ox, self._preview_w - self._box_px))
        self._by  = max(0, min(event.y - oy, self._preview_h - self._box_px))
        self._draw_overlay()

    def _on_use_crop(self):
        s    = self._preview_scale
        left = round(self._bx / s)
        top  = round(self._by / s)
        size = round(self._box_px / s)
        self.result = (left, top, left + size, top + size)
        self.destroy()

    def _on_use_center(self):
        self.result = None
        self.destroy()

    def _on_skip(self):
        self.cancelled = True
        self.destroy()

    def _on_cancel_all(self):
        self.cancelled  = True
        self.cancel_all = True
        self.destroy()


# ── Main application ───────────────────────────────────────────────────────────

class App(Tk):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.title("Image Resizer")
        self.resizable(False, False)
        self.config(bg=BG)
        self._build_ui()
        self._center_on_screen()

    def _center_on_screen(self):
        self.update_idletasks()
        w,  h  = 620, 500
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        p = {"padx": 12, "pady": 6}  # standard section padding

        self._build_input_row(**p)
        self._build_output_row(**p)
        self._build_size_row()
        self._build_progress_row()
        self._build_log_area()
        self._build_action_buttons()

    def _build_input_row(self, **p):
        frame = Frame(self, bg=BG)
        frame.pack(fill=X, **p)
        Label(frame, text="Input", font=("Segoe UI", 9, "bold"), bg=BG, fg=FG, anchor="w").pack(fill=X)

        row = Frame(frame, bg=BG)
        row.pack(fill=X, pady=(2, 0))
        self.input_var = StringVar()
        self._entry(row, self.input_var).pack(side=LEFT, expand=True, fill=X)
        self._btn(row, "Browse Folder", self._browse_input_folder).pack(side=LEFT, padx=(6, 0))
        self._btn(row, "Browse File",   self._browse_input_file  ).pack(side=LEFT, padx=(4, 0))

    def _build_output_row(self, **p):
        frame = Frame(self, bg=BG)
        frame.pack(fill=X, **p)
        Label(
            frame,
            text="Output folder  (Web and Insta subfolders will be created here)",
            font=("Segoe UI", 9, "bold"), bg=BG, fg=FG, anchor="w",
        ).pack(fill=X)

        row = Frame(frame, bg=BG)
        row.pack(fill=X, pady=(2, 0))
        self.output_var = StringVar()
        self._entry(row, self.output_var).pack(side=LEFT, expand=True, fill=X)
        self._btn(row, "Browse Folder", self._browse_output_folder).pack(side=LEFT, padx=(6, 0))

    def _build_size_row(self):
        frame = Frame(self, bg=BG)
        frame.pack(fill=X, padx=12, pady=(2, 4))

        Label(frame, text="Web long edge (px):", bg=BG, fg=FG, font=("Segoe UI", 9)).pack(side=LEFT)
        self.web_size_var = StringVar(value=str(DEFAULT_WEB_PX))
        self._entry(frame, self.web_size_var, width=6, justify="center").pack(side=LEFT, padx=(4, 20))

        Label(frame, text="Instagram size (px):", bg=BG, fg=FG, font=("Segoe UI", 9)).pack(side=LEFT)
        self.insta_size_var = StringVar(value=str(DEFAULT_INSTA_PX))
        self._entry(frame, self.insta_size_var, width=6, justify="center").pack(side=LEFT, padx=(4, 4))

        Label(frame, text="(square)", bg=BG, fg=FG_DIM, font=("Segoe UI", 8)).pack(side=LEFT)

    def _build_progress_row(self):
        frame = Frame(self, bg=BG)
        frame.pack(fill=X, padx=12, pady=(6, 2))

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "App.Horizontal.TProgressbar",
            troughcolor=BG_DARK, background=WEB_BTN,
            bordercolor=BG, lightcolor=WEB_BTN, darkcolor=WEB_BTN,
        )
        self.progress_bar = ttk.Progressbar(frame, mode="determinate", style="App.Horizontal.TProgressbar")
        self.progress_bar.pack(fill=X)

        self.status_label = Label(frame, text="Ready", anchor="w", fg=FG_DIM, bg=BG, font=("Segoe UI", 8))
        self.status_label.pack(fill=X, pady=(2, 0))

    def _build_log_area(self):
        frame = Frame(self, bg=BG)
        frame.pack(fill=BOTH, expand=True, padx=12, pady=(2, 6))

        scrollbar = Scrollbar(frame, bg=BTN_BG, troughcolor=BG_DARK, activebackground=BTN_ACT, relief="flat")
        scrollbar.pack(side=RIGHT, fill=Y)

        self.log = Text(
            frame, height=12, state=DISABLED, font=("Consolas", 8),
            yscrollcommand=scrollbar.set, bg=BG_DARK, fg=FG,
            insertbackground=FG, relief="flat", bd=1,
            selectbackground=BTN_BG, selectforeground=FG,
        )
        self.log.pack(fill=BOTH, expand=True)
        scrollbar.config(command=self.log.yview)

    def _build_action_buttons(self):
        frame = Frame(self, bg=BG)
        frame.pack(pady=(0, 12))

        self.web_btn = Button(
            frame, text="Resize for Web", command=self._start_web,
            font=("Segoe UI", 10, "bold"), width=18, height=2,
            bg=WEB_BTN, fg=FG, activebackground=WEB_ACT, activeforeground=FG,
            relief="flat", cursor="hand2",
        )
        self.web_btn.pack(side=LEFT, padx=6)

        self.insta_btn = Button(
            frame, text="Crop for Instagram", command=self._start_insta,
            font=("Segoe UI", 10, "bold"), width=18, height=2,
            bg=INST_BTN, fg=FG, activebackground=INST_ACT, activeforeground=FG,
            relief="flat", cursor="hand2",
        )
        self.insta_btn.pack(side=LEFT, padx=6)

    # ── Widget factories ───────────────────────────────────────────────────────

    def _entry(self, parent, var, width=50, justify="left"):
        return Entry(
            parent, textvariable=var, width=width, justify=justify,
            bg=BG_DARK, fg=FG, insertbackground=FG, relief="flat", bd=4,
        )

    def _btn(self, parent, text, command, width=13):
        return Button(
            parent, text=text, command=command, width=width,
            bg=BTN_BG, fg=FG, activebackground=BTN_ACT, activeforeground=FG,
            relief="flat", cursor="hand2",
        )

    # ── Browse callbacks ───────────────────────────────────────────────────────

    def _browse_input_folder(self):
        path = filedialog.askdirectory(title="Select input folder")
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(path)

    def _browse_input_file(self):
        path = filedialog.askopenfilename(
            title="Select JPEG file",
            filetypes=[("JPEG files", "*.jpg *.jpeg"), ("All files", "*.*")],
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

    # ── Validation helpers ─────────────────────────────────────────────────────

    def _get_files_and_output(self):
        """Return (files, out_root) after validating the input/output fields, or (None, None)."""
        input_path  = self.input_var.get().strip()
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

    def _get_sizes(self):
        """Return (web_px, insta_px) after validating the size fields, or (None, None)."""
        for label, var in [("Web", self.web_size_var), ("Instagram", self.insta_size_var)]:
            try:
                value = int(var.get().strip())
                if not 100 <= value <= 20000:
                    raise ValueError
            except ValueError:
                messagebox.showwarning(
                    "Invalid size",
                    f"{label} size must be a whole number between 100 and 20000.",
                )
                return None, None

        return int(self.web_size_var.get()), int(self.insta_size_var.get())

    # ── Button state ───────────────────────────────────────────────────────────

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

    # ── Finish summary ─────────────────────────────────────────────────────────

    def _finish(self, errors, done, skipped, manually_skipped, output_folder):
        self._log("")

        if errors:
            self._log(f"Completed with {len(errors)} error(s).")
            self.status_label.config(
                text=f"Done — {len(errors)} error(s). See log.", fg="#c0504d"
            )
        else:
            parts = []
            if done:             parts.append(f"{done} done")
            if skipped:          parts.append(f"{skipped} already existed")
            if manually_skipped: parts.append(f"{manually_skipped} skipped")
            summary = ", ".join(parts) + "."
            self._log(summary)
            self._log(f"Output → {output_folder}")
            self.status_label.config(text=summary, fg="#7caa6e")

        self._unlock_buttons()

    # ── Web resizing ───────────────────────────────────────────────────────────

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
        self.progress_bar["maximum"] = len(files)
        self.progress_bar["value"]   = 0
        self._log(f"Resizing {len(files)} file(s) for Web at {web_px}px long edge...\n")

        threading.Thread(target=self._run_web, args=(files, web_dir, web_px), daemon=True).start()

    def _run_web(self, files: list[Path], web_dir: Path, web_px: int):
        errors, done, skipped = [], 0, 0

        for i, fp in enumerate(files, 1):
            self.status_label.config(text=f"Processing {i}/{len(files)}: {fp.name}")
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

        self._finish(errors, done, skipped, 0, web_dir)

    # ── Instagram cropping ─────────────────────────────────────────────────────

    def _start_insta(self):
        files, out_root = self._get_files_and_output()
        if files is None:
            return
        _, insta_px = self._get_sizes()
        if insta_px is None:
            return

        self._lock_buttons()
        self._clear_log()

        insta_dir    = out_root / "Insta"
        saved_crops  = load_crops(insta_dir)
        todo         = [fp for fp in files if not (insta_dir / f"{fp.stem}_Insta.jpg").exists()]
        already_done = len(files) - len(todo)

        self.progress_bar["maximum"] = len(files)
        self.progress_bar["value"]   = already_done
        self._log(f"Cropping {len(todo)} file(s) for Instagram at {insta_px}×{insta_px}px...\n")

        # Show a crop dialog for each file that needs processing.
        # This must run on the main thread because dialogs are modal UI.
        new_crops = {}
        for i, fp in enumerate(todo, 1):
            prev     = saved_crops.get(fp.name)
            prev_box = tuple(prev) if prev is not None else None

            dlg = CropDialog(self, fp, i, len(todo), initial_crop=prev_box)
            self.wait_window(dlg)

            if dlg.cancel_all:
                self._log("Cancelled — no images processed.")
                self.status_label.config(text="Cancelled.", fg=FG_DIM)
                self._unlock_buttons()
                return

            new_crops[fp] = "skip" if dlg.cancelled else dlg.result

        threading.Thread(
            target=self._run_insta,
            args=(files, insta_dir, new_crops, already_done, saved_crops, insta_px),
            daemon=True,
        ).start()

    def _run_insta(self, files, insta_dir, new_crops, already_done, saved_crops, insta_px):
        errors, done, skipped, manually_skipped = [], 0, already_done, 0
        processed = already_done

        # Log files that were already done before this run
        for fp in files:
            if fp not in new_crops:
                self._log(f"  –  {fp.name}  (already exists, skipped)")

        for fp, crop in new_crops.items():
            processed += 1
            self.status_label.config(text=f"Processing {processed}/{len(files)}: {fp.name}")

            if crop == "skip":
                self._log(f"  –  {fp.name}  (skipped by user)")
                manually_skipped += 1
                self.progress_bar["value"] = processed
                continue

            try:
                result = process_insta(fp, insta_dir, insta_px, crop_box=crop)
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

        # Merge this session's crops into the saved file
        merged = {**saved_crops, **{
            (fp.name if isinstance(fp, Path) else fp): (list(v) if v is not None else None)
            for fp, v in new_crops.items()
            if v != "skip"
        }}
        save_crops(insta_dir, merged)

        self._finish(errors, done, skipped, manually_skipped, insta_dir)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
