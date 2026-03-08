# Image Resizer

A simple desktop GUI for resizing JPEG images to web and Instagram sizes, built with Python and Tkinter.

![UI colour scheme: 18% grey, neutral for colour-accurate work]

---

## Features

- **Web resize** — scales the long edge to a target pixel width (default 2000px), preserving aspect ratio
- **Instagram crop** — produces a square image (default 1080×1080px) with a draggable manual crop tool
- **Saved crop positions** — Instagram crop choices are remembered per-image and restored next session
- **Skip existing files** — already-processed images are never overwritten
- **100% JPEG quality** — `quality=100, subsampling=0` with full EXIF data preserved
- **18% grey UI** — neutral interface colour so your eyes don't adjust away from accurate image assessment

---

## Requirements

- Python 3.10+
- [Pillow](https://pillow.readthedocs.io/)

```bash
pip install Pillow
```

---

## Usage

```bash
python image_resizer.py
```

### Workflow

1. **Select input** — choose a folder of JPEGs or a single file
2. **Select output** — choose where the `Web` and `Insta` subfolders will be created
3. **Set sizes** (optional) — adjust the web long edge and Instagram square size if needed
4. Click **Resize for Web** and/or **Crop for Instagram**

### Output structure

```
output_folder/
├── Web/
│   ├── photo_Web.jpg
│   └── ...
└── Insta/
    ├── photo_Insta.jpg
    ├── ...
    └── crop_settings.json    ← saved crop positions
```

---

## Instagram crop dialog

When you click **Crop for Instagram**, a preview window opens for each image:

- **Drag** the white box to position your crop
- The **rule-of-thirds grid** helps with composition
- If a crop was saved from a previous session, the box is pre-positioned there

| Button | Action |
|---|---|
| **Use This Crop** | Save the current box position and continue |
| **Use Center Crop** | Auto-center the crop and continue |
| **Skip This Image** | Skip this file and move to the next |
| **Cancel All** | Stop the queue entirely |

Closing the dialog window also cancels the queue.

---

## Settings

| Setting | Default | Description |
|---|---|---|
| Web long edge | `2000` px | The longest side of the web output image |
| Instagram size | `1080` px | The width and height of the square Instagram output |

Both can be changed in the UI before processing.

---

## Notes

- Images smaller than the web target size are not upscaled
- EXIF metadata (camera, GPS, datetime) is preserved in all outputs
- CMYK and grayscale JPEGs are automatically converted to RGB
- Crop settings are saved to `Insta/crop_settings.json` in the output folder
