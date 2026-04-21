from __future__ import annotations

import colorsys
import shutil
import zipfile
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None  # type: ignore

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}


def extract_from_zip(src_path: Path, out_dir: Path, media_prefix: str) -> list[Path]:
    """Extract images from a ZIP-based Office doc (docx or xlsx)."""
    extracted = []
    try:
        with zipfile.ZipFile(src_path) as z:
            for name in z.namelist():
                if not name.startswith(media_prefix):
                    continue
                relative = name[len(media_prefix):]
                if not relative or '/' in relative:
                    continue  # skip subdirectories
                ext = Path(name).suffix.lower()
                if ext not in IMAGE_EXTS:
                    continue
                raw = z.read(name)
                # Same Riso Scotti trademark blob is embedded in xlsx/docx media (~33KB).
                if ext in _LOGO_SUFFIXES and is_xls_trademark_logo_blob_size(len(raw)):
                    continue
                idx = len(extracted)
                out_name = f"{src_path.stem}_{idx}{ext}"
                out_path = out_dir / out_name
                out_path.write_bytes(raw)
                extracted.append(out_path)
    except Exception as e:
        print(f"  Warning: could not read {src_path.name}: {e}")
    return extracted


MIN_IMAGE_BYTES = 500  # skip tiny blobs that are likely thumbnails or artifacts

# Legacy .xls embed a Riso Scotti trademark blob beside real photos. It appears
# as JPEG ~33390–33406 B (and similar PNG sizes). Do NOT use a wide 30–35KB band:
# real docx thumbs (e.g. ~31KB pasta, ~33KB Viander) must be kept.
XLS_TRADEMARK_LOGO_MIN = 33_000
XLS_TRADEMARK_LOGO_MAX = 33_500


def is_xls_trademark_logo_blob_size(size: int) -> bool:
    """True if byte length matches embedded trademark logos from .xls scans."""
    return XLS_TRADEMARK_LOGO_MIN <= size <= XLS_TRADEMARK_LOGO_MAX


_LOGO_SUFFIXES = {'.png', '.jpg', '.jpeg'}


def should_ignore_xls_trademark_logo_file(path: Path) -> bool:
    """Skip extracted files that match the .xls Scotti-logo size signature."""
    if path.suffix.lower() not in _LOGO_SUFFIXES:
        return False
    try:
        return is_xls_trademark_logo_blob_size(path.stat().st_size)
    except OSError:
        return True


def should_skip_truncated_xls_jpeg(path: Path) -> bool:
    """Drop .xls JPEGs that decode but look corrupt (grey trunc, banding, colour glitches)."""
    if path.suffix.lower() not in ('.jpg', '.jpeg'):
        return False
    try:
        return _jpeg_likely_decode_corrupt(path.read_bytes())
    except OSError:
        return True


# Back-compat for older imports
def should_ignore_extracted_png(path: Path) -> bool:
    return should_ignore_xls_trademark_logo_file(path)


JPEG_SOI = b'\xff\xd8\xff'
JPEG_EOI = b'\xff\xd9'


def _jpeg_slice_decodes_fully(blob: bytes) -> bool:
    """True if Pillow can decode the full JPEG bitstream (catches truncation)."""
    if Image is None or len(blob) < MIN_IMAGE_BYTES:
        return False
    try:
        with Image.open(BytesIO(blob)) as im:
            im.load()
        return True
    except Exception:
        return False


def _jpeg_row_uniform_band_score(im: Image.Image, w: int, h: int) -> int:
    """Count row groups with almost no horizontal variation (solid bands / glitch stripes)."""
    xs = range(0, w, max(1, w // 60))
    score = 0
    y = 0
    while y < h:
        row_std: list[int] = []
        for yy in range(y, min(y + 3, h)):
            vals = [im.getpixel((x, yy)) for x in xs]
            rs = [v[0] for v in vals]
            gs = [v[1] for v in vals]
            bs = [v[2] for v in vals]
            row_std.append(max(max(rs) - min(rs), max(gs) - min(gs), max(bs) - min(bs)))
        if max(row_std) < 8:
            score += 1
        y += 5
    return score


def _jpeg_sample_high_sat_and_extreme(im: Image.Image, w: int, h: int) -> tuple[float, float]:
    """Return (pct high HSV saturation, pct near-black/near-white extreme pixels)."""
    high_sat = 0
    extreme = 0
    total = 0
    y_step = max(1, h // 90)
    x_step = max(1, w // 90)
    for y in range(0, h, y_step):
        for x in range(0, w, x_step):
            R, G, B = im.getpixel((x, y))
            r, g, b = R / 255.0, G / 255.0, B / 255.0
            total += 1
            _h, s, v = colorsys.rgb_to_hsv(r, g, b)
            if s > 0.75 and v > 0.35:
                high_sat += 1
            if max(R, G, B) > 245 and min(R, G, B) < 40:
                extreme += 1
    if total == 0:
        return 0.0, 0.0
    return 100.0 * high_sat / total, 100.0 * extreme / total


def _jpeg_likely_decode_corrupt(blob: bytes) -> bool:
    """Detect JPEGs that Pillow loads but look wrong (common with damaged .xls embeds)."""
    if Image is None or len(blob) < MIN_IMAGE_BYTES:
        return False
    try:
        with Image.open(BytesIO(blob)) as im:
            im = im.convert('RGB')
            im.load()
    except Exception:
        return True
    w, h = im.size
    if h < 120 or w < 120:
        return False

    step = max(1, w // 55)

    def luma_std(y0: int, y1: int) -> float:
        vals: list[float] = []
        ystep = max(1, (y1 - y0) // 24)
        for y in range(y0, y1, ystep):
            for x in range(0, w, step):
                r, g, b = im.getpixel((x, y))
                vals.append(0.299 * r + 0.587 * g + 0.114 * b)
        if len(vals) < 8:
            return 0.0
        m = sum(vals) / len(vals)
        return (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5

    top = luma_std(0, max(1, h // 6))
    mid = luma_std(h // 3, int(h * 0.5))
    bot = luma_std(int(h * 0.86), h)
    if top > 15.0 and mid < 8.0 and bot < 8.0:
        return True
    if top > 15.0 and bot < 1.0:
        return True

    bands = _jpeg_row_uniform_band_score(im, w, h)
    hs_pct, ex_pct = _jpeg_sample_high_sat_and_extreme(im, w, h)
    if bands > 45 and (hs_pct > 18.0 or ex_pct >= 5.0):
        return True
    if hs_pct > 40.0:
        return True
    if ex_pct > 35.0:
        return True
    # Horizontal banding glitches without neon extremes (e.g. Arborio 5kg vacuum .xls):
    # many near-uniform row strips, modest dimensions, low sat/extreme counts.
    if (
        36 <= bands <= 56
        and hs_pct < 12.0
        and ex_pct < 5.0
        and min(w, h) <= 1100
        and max(w, h) <= 1300
    ):
        return True
    return False


def _all_jpeg_so_starts(data: bytes) -> list[int]:
    out: list[int] = []
    i = 0
    while True:
        j = data.find(JPEG_SOI, i)
        if j < 0:
            break
        out.append(j)
        i = j + 2
    return out


def _jpeg_eoi_end_indices(data: bytes, start: int, until: int) -> list[int]:
    """Byte offsets (exclusive) after each ``FF D9`` in ``data[start:until]``."""
    ends: list[int] = []
    pos = start + 3
    while pos < until:
        j = data.find(JPEG_EOI, pos, until)
        if j == -1:
            break
        ends.append(j + 2)
        pos = j + 2
    return ends


def _trim_jpeg_after_last_eoi(blob: bytes) -> bytes:
    """Drop padding after the final ``FF D9`` when the file still decodes."""
    j = blob.rfind(JPEG_EOI)
    if j < 0:
        return blob
    trimmed = blob[: j + 2]
    if len(trimmed) >= MIN_IMAGE_BYTES and _jpeg_slice_decodes_fully(trimmed):
        return trimmed
    return blob


def _best_jpeg_blob_in_range(data: bytes, start: int, until: int) -> bytes | None:
    """Pick best JPEG in [start, until): bounded EOI search + decode + visual check."""
    ends = _jpeg_eoi_end_indices(data, start, until)
    if not ends:
        return None
    candidates = [data[start:e] for e in ends if e - start >= MIN_IMAGE_BYTES]
    candidates.sort(key=len, reverse=True)
    usable = [b for b in candidates if not is_xls_trademark_logo_blob_size(len(b))]
    if not usable:
        return None
    if Image is None:
        return usable[0]
    for blob in usable:
        if not _jpeg_slice_decodes_fully(blob):
            continue
        if _jpeg_likely_decode_corrupt(blob):
            continue
        return blob
    return None


def extract_from_xls(src_path: Path, out_dir: Path) -> list[Path]:
    """Extract images from a legacy .xls by scanning raw bytes for image magic.

    JPEG streams in .xls are often back-to-back; slicing only to the first
    ``FF D9`` or past the next file yields truncated or merged garbage. We
    split on JPEG SOI markers, try each ``[SOI_i : SOI_{i+1})`` as one image,
    then fall back to EOI search **within** that slice only.
    """
    data = src_path.read_bytes()
    extracted = []
    idx = 0

    # ── JPEG ────────────────────────────────────────────────────────────────
    sois = _all_jpeg_so_starts(data)
    for k, start in enumerate(sois):
        until = sois[k + 1] if k + 1 < len(sois) else len(data)
        segment = data[start:until]
        blob: bytes | None = None

        if (
            len(segment) >= MIN_IMAGE_BYTES
            and not is_xls_trademark_logo_blob_size(len(segment))
            and Image is not None
        ):
            try:
                with Image.open(BytesIO(segment)) as im:
                    im.load()
                if not _jpeg_likely_decode_corrupt(segment):
                    blob = _trim_jpeg_after_last_eoi(segment)
            except Exception:
                blob = None

        if blob is None:
            blob = _best_jpeg_blob_in_range(data, start, until)

        if blob:
            out_path = out_dir / f"{src_path.stem}_{idx}.jpg"
            out_path.write_bytes(blob)
            extracted.append(out_path)
            idx += 1

    # ── PNG: 89 50 4E 47 … 49 45 4E 44 AE 42 60 82 ─────────────────────────
    PNG_START = b'\x89PNG\r\n\x1a\n'
    PNG_END   = b'IEND\xaeB`\x82'
    pos = 0
    while True:
        start = data.find(PNG_START, pos)
        if start == -1:
            break
        end = data.find(PNG_END, start)
        if end == -1:
            break
        end += len(PNG_END)
        blob = data[start:end]
        if len(blob) >= MIN_IMAGE_BYTES and not is_xls_trademark_logo_blob_size(len(blob)):
            out_path = out_dir / f"{src_path.stem}_{idx}.png"
            out_path.write_bytes(blob)
            extracted.append(out_path)
            idx += 1
        pos = end

    return extracted


def copy_loose_image(src_path: Path, out_dir: Path) -> Path | None:
    """Copy a standalone image file into out_dir, avoiding name collisions."""
    dest = out_dir / src_path.name
    if dest.exists():
        dest = out_dir / f"{src_path.parent.name}_{src_path.name}"
    if dest.exists():
        # Still collides — append incrementing counter
        stem = src_path.stem
        ext  = src_path.suffix
        idx  = 1
        while dest.exists():
            dest = out_dir / f"{src_path.parent.name}_{stem}_{idx}{ext}"
            idx += 1
    shutil.copy2(src_path, dest)
    return dest


def main(media_dir: Path, out_dir: Path) -> int:
    """Walk media_dir, extract all images into out_dir. Returns total count."""
    out_dir.mkdir(exist_ok=True)
    total = 0

    for path in sorted(media_dir.rglob('*')):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        extracted = []

        if ext == '.docx':
            extracted = extract_from_zip(path, out_dir, 'word/media/')
        elif ext == '.xlsx':
            extracted = extract_from_zip(path, out_dir, 'xl/media/')
        elif ext == '.xls':
            extracted = extract_from_xls(path, out_dir)
        elif ext in IMAGE_EXTS:
            result = copy_loose_image(path, out_dir)
            if result:
                extracted = [result]

        if extracted:
            print(f"  {path.name}: {len(extracted)} image(s)")
            total += len(extracted)

    print(f"\nTotal: {total} image(s) extracted to {out_dir}/")
    return total


if __name__ == '__main__':
    import sys
    media = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('MEDIA-20260415T015452Z-3-001/MEDIA/fotos pagina web')
    out   = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('extracted_images')
    main(media, out)
