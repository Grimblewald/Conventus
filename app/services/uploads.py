"""Upload handling: Pillow validation, PDF magic check, safe filenames, crop."""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


log = logging.getLogger(__name__)


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
PDF_EXTS = {".pdf"}
FIGURE_EXTS = IMAGE_EXTS | {".tif", ".tiff"} | PDF_EXTS

# Generous upper bound for any decoded image dimension. Blocks decompression
# bombs while still allowing legit hi-res hero shots.
MAX_IMAGE_PIXELS = 24_000_000  # 24 megapixels
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

# Pillow warns above MAX_IMAGE_PIXELS and raises above twice it, so an image
# between the two is opened and caught by our own dimension check, while
# anything past the hard limit fails inside `Image.open` instead.
MAX_MEGAPIXELS = MAX_IMAGE_PIXELS // 1_000_000


def _too_many_pixels_message(pixels: int | None = None) -> str:
    """What to tell someone whose image is too big to decode.

    "Image is too large" invites them to compress the file, which changes the
    byte count and not the pixel count, so they try repeatedly and it fails
    every time. Say which measure is at fault and what to do about it.
    """
    actual = f"That image is {pixels / 1_000_000:.0f} megapixels" if pixels \
        else "That image has too many pixels"
    return (
        f"{actual} — the limit is {MAX_MEGAPIXELS} megapixels "
        f"(about 6000 × 4000). This is about the image's dimensions, not its "
        f"file size, so compressing it will not help: resize it to smaller "
        f"dimensions and upload it again."
    )


def _bomb_pixels(exc: Exception) -> int | None:
    """The pixel count out of Pillow's message, when it can be found."""
    import re

    m = re.search(r"\((\d+) pixels\)", str(exc))
    return int(m.group(1)) if m else None



class UploadError(ValueError):
    """Friendly error surfaced to the user via flash()."""


def _checked_ext(fs: FileStorage, allowed: Iterable[str]) -> str:
    ext = os.path.splitext(fs.filename or "")[1].lower()
    if ext not in set(allowed):
        raise UploadError(
            f"Unsupported file type {ext or '(no extension)'}. "
            f"Allowed: {', '.join(sorted(allowed))}."
        )
    return ext


def _safe_name(prefix: str, original: str | None) -> str:
    base = secure_filename(original or "file")
    return f"{prefix}_{secrets.token_hex(6)}_{base}"


def save_image(
    fs: FileStorage,
    *,
    upload_folder: str,
    subdir: str = "",
    prefix: str = "img",
    max_bytes: int,
    square_crop: bool = False,
    target_size: int | None = None,
    force_webp: bool = False,
) -> str:
    """Validate, optionally crop, and save an uploaded image.

    Returns the relative filename within `upload_folder` (including subdir
    if provided). Raises `UploadError` on any validation failure — never
    saves a corrupt or oversized file.
    """
    if not (fs and fs.filename):
        raise UploadError("No file was uploaded.")
    ext = _checked_ext(fs, IMAGE_EXTS)

    raw = fs.stream.read()
    if len(raw) > max_bytes:
        raise UploadError(
            f"File is too large ({len(raw) // 1024} KB). "
            f"Maximum is {max_bytes // 1024} KB."
        )

    # Validate with Pillow — this catches truncated files & decompression bombs.
    from io import BytesIO
    try:
        img = Image.open(BytesIO(raw))
        img.verify()  # cheap structural check
        # Re-open for actual ops — verify() consumes the file pointer.
        img = Image.open(BytesIO(raw))
        img = ImageOps.exif_transpose(img)
    except Image.DecompressionBombError as e:
        # NOT an OSError, so the clause below never caught it and the upload
        # became a 500 with an unhelpful page.
        raise UploadError(_too_many_pixels_message(_bomb_pixels(e))) from e
    except (UnidentifiedImageError, OSError) as e:
        raise UploadError("Could not read that image — is it corrupted?") from e

    if img.width * img.height > MAX_IMAGE_PIXELS:
        raise UploadError(_too_many_pixels_message(img.width * img.height))

    if square_crop:
        side = min(img.width, img.height)
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))

    if target_size and img.width > target_size:
        ratio = target_size / img.width
        img = img.resize((target_size, int(img.height * ratio)), Image.LANCZOS)

    # Always re-encode (don't trust the input bytes) and normalise.
    # Default: PNG originals stay as PNG (lossless), everything else becomes WEBP.
    # When force_webp is True, everything becomes WEBP regardless of original format.
    out_ext = ".webp" if force_webp or ext != ".png" else ".png"
    safe = _safe_name(prefix, (fs.filename or "image") + out_ext)
    if not safe.endswith(out_ext):
        safe += out_ext

    target_dir = Path(upload_folder) / subdir if subdir else Path(upload_folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / safe

    save_kwargs = {"optimize": True}
    if out_ext == ".webp":
        save_kwargs["quality"] = 88
        save_kwargs["method"] = 6
    img.save(out_path, format="WEBP" if out_ext == ".webp" else "PNG", **save_kwargs)

    return f"{subdir}/{safe}" if subdir else safe


def save_pdf(
    fs: FileStorage,
    *,
    upload_folder: str,
    subdir: str = "",
    prefix: str = "doc",
    max_bytes: int,
) -> str:
    """Validate the %PDF- magic prefix and save."""
    if not (fs and fs.filename):
        raise UploadError("No file was uploaded.")
    _checked_ext(fs, PDF_EXTS)
    raw = fs.stream.read()
    if len(raw) > max_bytes:
        raise UploadError(
            f"File is too large ({len(raw) // 1024} KB). "
            f"Maximum is {max_bytes // 1024} KB."
        )
    if not raw[:5] == b"%PDF-":
        raise UploadError("File is not a valid PDF (missing %PDF- header).")

    safe = _safe_name(prefix, fs.filename)
    target_dir = Path(upload_folder) / subdir if subdir else Path(upload_folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / safe).write_bytes(raw)
    return f"{subdir}/{safe}" if subdir else safe


def save_figure(
    fs: FileStorage,
    *,
    upload_folder: str,
    max_bytes: int,
) -> str:
    """Abstract figure: accept PNG/JPEG/TIFF/PDF, validated."""
    if not (fs and fs.filename):
        raise UploadError("No figure uploaded.")
    ext = _checked_ext(fs, FIGURE_EXTS)
    if ext in PDF_EXTS:
        return save_pdf(fs, upload_folder=upload_folder, subdir="abstracts",
                        prefix="fig", max_bytes=max_bytes)
    # Image-typed figure
    fs.stream.seek(0)
    return save_image(
        fs,
        upload_folder=upload_folder,
        subdir="abstracts",
        prefix="fig",
        max_bytes=max_bytes,
        target_size=2400,
    )


def save_fixed_png(
    fs: FileStorage,
    *,
    dest_dir: str | Path,
    name: str,
    max_bytes: int,
    max_width: int = 1600,
) -> str:
    """Validate an uploaded image and save it as `<name>.png` at a fixed path,
    replacing any previous file (single-slot asset — no orphan accumulation).

    PNG on purpose: these assets feed the LaTeX document renderer, and
    graphicx reads png/jpg but not webp. Returns the filename written.
    """
    if not (fs and fs.filename):
        raise UploadError("No file was uploaded.")
    _checked_ext(fs, IMAGE_EXTS)

    raw = fs.stream.read()
    if len(raw) > max_bytes:
        raise UploadError(
            f"File is too large ({len(raw) // 1024} KB). "
            f"Maximum is {max_bytes // 1024} KB."
        )

    from io import BytesIO
    try:
        img = Image.open(BytesIO(raw))
        img.verify()
        img = Image.open(BytesIO(raw))
        img = ImageOps.exif_transpose(img)
    except Image.DecompressionBombError as e:
        # NOT an OSError, so the clause below never caught it and the upload
        # became a 500 with an unhelpful page.
        raise UploadError(_too_many_pixels_message(_bomb_pixels(e))) from e
    except (UnidentifiedImageError, OSError) as e:
        raise UploadError("Could not read that image — is it corrupted?") from e

    if img.width * img.height > MAX_IMAGE_PIXELS:
        raise UploadError(_too_many_pixels_message(img.width * img.height))

    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    filename = f"{name}.png"
    # Re-encode (never trust input bytes); RGBA keeps logo/signature
    # transparency intact for the PDF.
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    img.save(dest / filename, format="PNG", optimize=True)
    return filename


def remove_upload(upload_folder: str, name: str | None) -> None:
    """Best-effort delete of a relative upload path. Silently ignores errors."""
    if not name:
        return
    try:
        path = Path(upload_folder) / name
        if path.exists():
            path.unlink()
    except OSError:
        log.warning("Could not remove upload %r", name, exc_info=True)
