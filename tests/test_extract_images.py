import zipfile
from io import BytesIO
from pathlib import Path
import pytest

from PIL import Image


# ── helpers ────────────────────────────────────────────────────────────────

TINY_JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b'\x00' * 200 + bytes([0xFF, 0xD9])
TINY_PNG  = b'\x89PNG\r\n\x1a\n' + b'\x00' * 200 + b'IEND\xaeB`\x82'


def minimal_valid_jpeg_bytes() -> bytes:
    """Real JPEG bitstream so Pillow can validate extraction from .xls."""
    im = Image.new('RGB', (8, 8), color=(200, 10, 10))
    buf = BytesIO()
    im.save(buf, format='JPEG', quality=85)
    return buf.getvalue()


def make_zip(path: Path, files: dict[str, bytes]):
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)


@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "extracted"
    d.mkdir()
    return d


# ── extract_from_zip ────────────────────────────────────────────────────────

def test_extract_from_docx_returns_jpeg(tmp_path, out_dir):
    from extract_images import extract_from_zip
    docx = tmp_path / "product.docx"
    make_zip(docx, {"word/media/image1.jpg": TINY_JPEG, "word/document.xml": b"<x/>"})
    result = extract_from_zip(docx, out_dir, "word/media/")
    assert len(result) == 1
    assert result[0].suffix == ".jpg"
    assert result[0].read_bytes() == TINY_JPEG


def test_extract_from_xlsx_returns_png(tmp_path, out_dir):
    from extract_images import extract_from_zip
    xlsx = tmp_path / "sheet.xlsx"
    make_zip(xlsx, {"xl/media/image1.png": TINY_PNG, "[Content_Types].xml": b""})
    result = extract_from_zip(xlsx, out_dir, "xl/media/")
    assert len(result) == 1
    assert result[0].suffix == ".png"


def test_extract_from_zip_skips_trademark_logo_blob(tmp_path, out_dir):
    """xlsx/docx embed the same ~33.4KB Riso Scotti media as legacy .xls."""
    from extract_images import extract_from_zip

    PNG_END = b'IEND\xaeB`\x82'
    PNG_START = b'\x89PNG\r\n\x1a\n'
    target = 33_390
    logo_png = PNG_START + (b'\x00' * (target - len(PNG_START) - len(PNG_END))) + PNG_END
    assert len(logo_png) == target

    xlsx = tmp_path / "Risotto con trufas.xlsx"
    make_zip(xlsx, {"xl/media/image1.png": logo_png, "[Content_Types].xml": b""})
    result = extract_from_zip(xlsx, out_dir, "xl/media/")
    assert result == []


def test_extract_from_zip_keeps_next_image_after_skipping_logo(tmp_path, out_dir):
    from extract_images import extract_from_zip

    PNG_END = b'IEND\xaeB`\x82'
    PNG_START = b'\x89PNG\r\n\x1a\n'
    logo = PNG_START + (b'\x00' * (33_400 - len(PNG_START) - len(PNG_END))) + PNG_END
    good = PNG_START + (b'\x42' * 2000) + PNG_END

    xlsx = tmp_path / "Jasmine rice 500g.xlsx"
    make_zip(xlsx, {
        "xl/media/a.png": logo,
        "xl/media/b.png": good,
        "[Content_Types].xml": b"",
    })
    result = extract_from_zip(xlsx, out_dir, "xl/media/")
    assert len(result) == 1
    assert result[0].read_bytes() == good


def test_extract_from_zip_skips_non_image(tmp_path, out_dir):
    from extract_images import extract_from_zip
    docx = tmp_path / "doc.docx"
    make_zip(docx, {"word/media/embed.xml": b"<xml/>", "word/document.xml": b"<x/>"})
    result = extract_from_zip(docx, out_dir, "word/media/")
    assert result == []


def test_extract_from_zip_skips_subdirs(tmp_path, out_dir):
    from extract_images import extract_from_zip
    docx = tmp_path / "doc.docx"
    make_zip(docx, {"word/media/sub/image1.jpg": TINY_JPEG})
    result = extract_from_zip(docx, out_dir, "word/media/")
    assert result == []


def test_extract_from_zip_corrupt_file_returns_empty(tmp_path, out_dir):
    from extract_images import extract_from_zip
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    result = extract_from_zip(bad, out_dir, "word/media/")
    assert result == []


def test_extract_from_zip_multiple_images(tmp_path, out_dir):
    from extract_images import extract_from_zip
    docx = tmp_path / "multi.docx"
    make_zip(docx, {
        "word/media/image1.jpg": TINY_JPEG,
        "word/media/image2.png": TINY_PNG,
    })
    result = extract_from_zip(docx, out_dir, "word/media/")
    assert len(result) == 2


# ── extract_from_xls ────────────────────────────────────────────────────────

def make_xls_with_jpeg(path: Path):
    """Write a fake binary file containing a JPEG blob preceded by junk."""
    jpeg = minimal_valid_jpeg_bytes()
    path.write_bytes(b'\x00' * 512 + jpeg + b'\x00' * 128)


def make_xls_with_png(path: Path):
    png = b'\x89PNG\r\n\x1a\n' + b'\x42' * 2000 + b'IEND\xaeB`\x82'
    path.write_bytes(b'\x00' * 512 + png + b'\x00' * 128)


def test_extract_from_xls_finds_jpeg(tmp_path, out_dir):
    from extract_images import extract_from_xls
    xls = tmp_path / "product.xls"
    make_xls_with_jpeg(xls)
    result = extract_from_xls(xls, out_dir)
    assert len(result) == 1
    assert result[0].suffix == ".jpg"


def test_extract_from_xls_finds_png(tmp_path, out_dir):
    from extract_images import extract_from_xls
    xls = tmp_path / "product.xls"
    make_xls_with_png(xls)
    result = extract_from_xls(xls, out_dir)
    assert len(result) == 1
    assert result[0].suffix == ".png"


def test_extract_from_xls_empty_returns_empty(tmp_path, out_dir):
    from extract_images import extract_from_xls
    xls = tmp_path / "empty.xls"
    xls.write_bytes(b'\x00' * 512)
    result = extract_from_xls(xls, out_dir)
    assert result == []


def test_extract_from_xls_skips_tiny_blobs(tmp_path, out_dir):
    from extract_images import extract_from_xls
    xls = tmp_path / "tiny.xls"
    # JPEG that's only 10 bytes — below the 500-byte threshold
    xls.write_bytes(bytes([0xFF, 0xD8, 0xFF]) + b'\x00' * 5 + bytes([0xFF, 0xD9]))
    result = extract_from_xls(xls, out_dir)
    assert result == []


def test_extract_from_xls_skips_trademark_logo_png_size(tmp_path, out_dir):
    """Riso Scotti–sized PNG blobs from legacy .xls must not be written (~33.0–33.5KB)."""
    from extract_images import extract_from_xls

    PNG_END = b'IEND\xaeB`\x82'
    PNG_START = b'\x89PNG\r\n\x1a\n'
    target = 33_406
    body_len = target - len(PNG_START) - len(PNG_END)
    logo_png = PNG_START + (b'\x00' * body_len) + PNG_END
    assert len(logo_png) == target

    xls = tmp_path / "Basmati rice 500g.xls"
    xls.write_bytes(b'\x00' * 512 + logo_png + b'\x00' * 128)
    result = extract_from_xls(xls, out_dir)
    assert result == []


def test_extract_from_xls_skips_trademark_logo_jpeg_size(tmp_path, out_dir):
    """Same trademark blobs often appear as JPEG from .xls byte scans."""
    from extract_images import extract_from_xls

    target = 33_390
    bad_jpg = b'\xff\xd8\xff' + (b'\x00' * (target - 5)) + b'\xff\xd9'
    assert len(bad_jpg) == target

    xls = tmp_path / "Risotto con trufas.xls"
    xls.write_bytes(b'\x00' * 512 + bad_jpg + b'\x00' * 128)
    result = extract_from_xls(xls, out_dir)
    assert result == []


def test_extract_from_xls_keeps_png_after_skipping_logo_blob(tmp_path, out_dir):
    from extract_images import extract_from_xls

    PNG_END = b'IEND\xaeB`\x82'
    PNG_START = b'\x89PNG\r\n\x1a\n'
    logo_len = 33_400
    logo_png = PNG_START + (b'\x00' * (logo_len - len(PNG_START) - len(PNG_END))) + PNG_END
    good_png = PNG_START + (b'\x42' * 2000) + PNG_END

    xls = tmp_path / "rice.xls"
    xls.write_bytes(b'\x00' * 100 + logo_png + b'PAD' + good_png + b'\x00' * 100)
    result = extract_from_xls(xls, out_dir)
    assert len(result) == 1
    assert result[0].suffix == ".png"
    assert result[0].stat().st_size == len(good_png)


def test_extract_from_xls_keeps_jpeg_after_skipping_logo_blob(tmp_path, out_dir):
    from extract_images import extract_from_xls

    bad_len = 33_406
    bad_jpg = b'\xff\xd8\xff' + (b'\x00' * (bad_len - 5)) + b'\xff\xd9'
    good_jpg = minimal_valid_jpeg_bytes()

    xls = tmp_path / "Jasmine rice 500g.xls"
    xls.write_bytes(b'\x00' * 100 + bad_jpg + b'XX' + good_jpg + b'\x00' * 100)
    result = extract_from_xls(xls, out_dir)
    assert len(result) == 1
    assert result[0].suffix == ".jpg"
    assert result[0].stat().st_size == len(good_jpg)


# ── copy_loose_image ────────────────────────────────────────────────────────

def test_copy_loose_image_copies_file(tmp_path, out_dir):
    from extract_images import copy_loose_image
    img = tmp_path / "salov foto.png"
    img.write_bytes(TINY_PNG)
    result = copy_loose_image(img, out_dir)
    assert result is not None
    assert result.exists()
    assert result.read_bytes() == TINY_PNG


def test_copy_loose_image_avoids_overwrite(tmp_path, out_dir):
    from extract_images import copy_loose_image
    img = tmp_path / "photo.jpg"
    img.write_bytes(TINY_JPEG)
    (out_dir / "photo.jpg").write_bytes(b"existing")
    result = copy_loose_image(img, out_dir)
    assert result is not None
    assert (out_dir / "photo.jpg").read_bytes() == b"existing"  # original untouched
    assert result.read_bytes() == TINY_JPEG  # written under alternate name


# ── main orchestrator ───────────────────────────────────────────────────────

def test_main_extracts_from_nested_dirs(tmp_path, out_dir):
    from extract_images import main as extract_main
    media = tmp_path / "media"
    sub = media / "sub"
    sub.mkdir(parents=True)

    docx = sub / "product.docx"
    make_zip(docx, {"word/media/image1.jpg": TINY_JPEG})

    loose = media / "photo.png"
    loose.write_bytes(TINY_PNG)

    total = extract_main(media, out_dir)
    assert total == 2
    assert len(list(out_dir.iterdir())) == 2


def test_main_dispatches_xls(tmp_path, out_dir):
    from extract_images import main as extract_main
    media = tmp_path / "media"
    media.mkdir()

    xls = media / "product.xls"
    jpeg = minimal_valid_jpeg_bytes()
    xls.write_bytes(b'\x00' * 512 + jpeg + b'\x00' * 128)

    total = extract_main(media, out_dir)
    assert total == 1
    extracted = list(out_dir.iterdir())
    assert len(extracted) == 1
    assert extracted[0].suffix == ".jpg"
