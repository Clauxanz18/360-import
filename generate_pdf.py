from __future__ import annotations

import asyncio
import base64
import csv
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright

from extract_images import should_ignore_extracted_png, should_skip_truncated_xls_jpeg


# ── Pure helpers ─────────────────────────────────────────────────────────────

def get_initials(name: str) -> str:
    """Return up to 3 uppercase initials from words that start with a letter."""
    words = name.split()
    initials = [w[0].upper() for w in words if w and w[0].isalpha()]
    return ''.join(initials[:3])


def encode_image(image_path: str) -> str | None:
    """Return a base64 data URI for the image, or None if missing/FALSE."""
    if not image_path or image_path == 'FALSE':
        return None
    p = Path(image_path)
    if not p.exists() or should_ignore_extracted_png(p) or should_skip_truncated_xls_jpeg(p):
        return None
    suffix = p.suffix.lower().lstrip('.')
    if suffix == 'jpg':
        suffix = 'jpeg'
    data = base64.b64encode(p.read_bytes()).decode()
    return f"data:image/{suffix};base64,{data}"


def build_pages(products: list[dict], page_size: int = 3) -> list[list[dict]]:
    """
    Sort products by (Categoria, Producto_Base), annotate each with
    show_banner=True on category change, then chunk into pages of page_size.
    """
    sorted_prods = sorted(
        products,
        key=lambda p: (p.get('Categoria', '').lower(), p.get('Producto_Base', '').lower())
    )
    last_cat = None
    annotated = []
    for p in sorted_prods:
        item = dict(p)
        cat = item.get('Categoria', '')
        item['show_banner'] = (cat != last_cat)
        item['image_data']  = encode_image(item.get('Imagenes', 'FALSE'))
        item['initials']    = get_initials(item.get('Producto_Base', item.get('Producto', '')))
        last_cat = cat
        annotated.append(item)

    return [annotated[i:i + page_size] for i in range(0, len(annotated), page_size)]


def build_category_index(pages: list[list[dict]]) -> list[dict]:
    """
    Return [{name, page}, ...] for each category's first content page (1-based).
    Only records categories at the moment their banner first appears.
    """
    seen: dict[str, int] = {}
    for page_num, page_items in enumerate(pages, start=1):
        for item in page_items:
            cat = item.get('Categoria', '')
            if cat and cat not in seen and item.get('show_banner'):
                seen[cat] = page_num
    return [{'name': cat, 'page': pg} for cat, pg in seen.items()]


def render_html(pages: list[list[dict]], template_path: Path) -> str:
    """Render the Jinja2 template with page data."""
    env = Environment(loader=FileSystemLoader(str(template_path.parent)))
    template = env.get_template(template_path.name)
    category_index = build_category_index(pages)
    return template.render(pages=pages, total_pages=len(pages), category_index=category_index)


# ── Playwright PDF ────────────────────────────────────────────────────────────

async def html_to_pdf(html: str, output_path: Path) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page    = await browser.new_page()
        await page.set_content(html)
        await page.wait_for_load_state('networkidle')
        await page.pdf(
            path=str(output_path),
            format='A4',
            print_background=True,
            margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'},
        )
        await browser.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def load_products(csv_path: Path) -> list[dict]:
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def main(
    csv_path: Path      = Path('productos_con_imagenes.csv'),
    template_path: Path = Path('catalog_template.html'),
    output_path: Path   = Path('catalogo_360import.pdf'),
) -> None:
    products = load_products(csv_path)
    print(f"Loaded {len(products)} products")

    pages = build_pages(products)
    print(f"Built {len(pages)} page(s)")

    html = render_html(pages, template_path)
    print(f"HTML rendered ({len(html):,} chars)")

    asyncio.run(html_to_pdf(html, output_path))
    print(f"PDF saved → {output_path}")


if __name__ == '__main__':
    main(
        csv_path      = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('productos_con_imagenes.csv'),
        template_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('catalog_template.html'),
        output_path   = Path(sys.argv[3]) if len(sys.argv) > 3 else Path('catalogo_360import.pdf'),
    )
