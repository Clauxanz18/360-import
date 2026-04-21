from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from rapidfuzz import fuzz, process

from extract_images import should_ignore_extracted_png, should_skip_truncated_xls_jpeg

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}

# Files to exclude from matching (logos, banners, etc.)
EXCLUDED_STEMS = {'LOGO 360 IMPORT', 'LOGO 360'}

# Spanish ↔ English equivalents for key food terms.
# Each key maps to a list of terms that should be considered identical.
TERM_EQUIVALENTS: dict[str, list[str]] = {
    'arroz':       ['rice', 'arroz'],
    'arroces':     ['rice', 'arroz'],
    'rice':        ['arroz', 'rice'],
    'harina':      ['flour', 'harina', 'farina'],
    'harinas':     ['flour', 'harina', 'farina'],
    'farina':      ['harina', 'flour', 'farina'],
    'flour':       ['harina', 'farina', 'flour'],
    'galleta':     ['cookie', 'biscuit', 'galleta'],
    'galletas':    ['cookies', 'biscuits', 'galleta', 'galletas'],
    'cookie':      ['galleta', 'cookie'],
    'biscuit':     ['galleta', 'biscuit'],
    'tomate':      ['tomato', 'tomate'],
    'tomates':     ['tomatoes', 'tomate', 'tomates'],
    'tomato':      ['tomate', 'tomato'],
    'tomatoes':    ['tomates', 'tomato'],
    'hongos':      ['mushroom', 'funghi', 'hongos'],
    'mushroom':    ['hongos', 'funghi', 'mushroom'],
    'funghi':      ['hongos', 'mushroom', 'funghi'],
    'trufa':       ['truffle', 'trufa'],
    'trufas':      ['truffle', 'trufa', 'trufas'],
    'truffle':     ['trufa', 'truffle'],
    'queso':       ['cheese', 'queso'],
    'cheese':      ['queso', 'cheese'],
    'pelados':     ['peeled', 'pelados'],
    'peeled':      ['pelados', 'peeled'],
    'parmesana':   ['parmigiana', 'parmesan', 'parmesana'],
    'parmigiana':  ['parmesana', 'parmesan', 'parmigiana'],
    'parmeggiano': ['parmesan', 'parmeggiano', 'parmigiano'],
    'parmesan':    ['parmesana', 'parmeggiano', 'parmesan'],
    'semola':      ['semolina', 'semola'],
    'semolina':    ['semola', 'semolina'],
    'miel':        ['honey', 'miel'],
    'honey':       ['miel', 'honey'],
    'aceite':      ['oil', 'aceite'],
    'oil':         ['aceite', 'oil'],
    'vinagre':     ['vinegar', 'vinagre'],
    'vinegar':     ['vinagre', 'vinegar'],
    # Brand / product names already the same in both languages — listed so
    # they survive normalization and are treated as strong match signals.
    'arborio':    ['arborio'],
    'carnaroli':  ['carnaroli'],
    'basmati':    ['basmati'],
    'jasmine':    ['jasmine'],
    'venere':     ['venere'],
    'risotto':    ['risotto'],
    'porcini':    ['porcini'],
    'loacker':    ['loacker'],
    'bonomi':     ['bonomi'],
    'gemignani':  ['gemignani'],
    'scotti':     ['scotti'],
    'granoro':    ['granoro'],
    'viander':    ['viander'],
    'salov':      ['salov'],
    'biase':      ['biase'],
    'lotus':      ['lotus'],
    'biscoff':    ['biscoff'],
    'grana':      ['grana'],
    'provolone':  ['provolone'],
    'forcello':   ['forcello'],
    'padano':     ['padano'],
    'reggiano':   ['reggiano'],
    'passata':    ['passata'],
    'pomodoro':   ['tomate', 'tomato', 'pomodoro'],
    'pesto':      ['pesto', 'salov'],
    'salov':      ['salov', 'pesto'],
}

# When direct fuzzy matching fails, fall back to this image base stem for
# products in the given category.
CATEGORY_FALLBACKS: dict[str, str] = {
    'pastas':                  'FOTO PASTAS',
    'pastas trafiladas al bronzo': 'FOTO PASTAS',
    'pastas sin gluten':       'FOTO PASTAS',
    'Pastas integrales':       'FOTO PASTAS',
    'pastas integrales':       'FOTO PASTAS',
    'Cous cous':               'FOTO PASTAS',
    'salsa pesto':             'salov foto',
    'salsa pesto pistacho':    'fotos viander',
}

_SIZE_RE  = re.compile(r'\b\d+\s*(?:g|gr|kg|ml|l|lt|x\s*\d+|pcs|un|libras?)\b', re.I)
_PUNCT_RE = re.compile(r'[^\w\s]')
_SPACE_RE = re.compile(r'\s+')
_SUFFIX_RE = re.compile(r'^(.*?)_(\d+)$')


# ── Text helpers ──────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Lowercase, strip sizes and punctuation, collapse whitespace."""
    text = text.lower()
    text = _SIZE_RE.sub(' ', text)
    text = _PUNCT_RE.sub(' ', text)
    return _SPACE_RE.sub(' ', text).strip()


def expand_terms(text: str) -> str:
    """Append translated/equivalent forms of known food terms to text."""
    words = normalize(text).split()
    seen = set(words)
    extra: list[str] = []
    for w in words:
        for eq in TERM_EQUIVALENTS.get(w, []):
            if eq not in seen:
                extra.append(eq)
                seen.add(eq)
    return ' '.join(words + extra)


# ── Image index ───────────────────────────────────────────────────────────────

def build_deduplicated_index(images_dir: Path) -> dict[str, Path]:
    """
    Index images by base name, deduplicating _N numeric variants.
    e.g. galletas loacker_0.png … galletas loacker_24.png → 'galletas loacker'
    The file with the lowest N (or -1 for no suffix) is kept.
    Returns {base_stem: path}.
    """
    groups: dict[str, list[tuple[int, Path]]] = {}
    for p in images_dir.iterdir():
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
            continue
        if p.stem.strip() in EXCLUDED_STEMS:
            continue
        if should_ignore_extracted_png(p):
            continue
        if should_skip_truncated_xls_jpeg(p):
            continue
        m = _SUFFIX_RE.match(p.stem)
        base = m.group(1).strip() if m else p.stem.strip()
        idx  = int(m.group(2))   if m else -1
        groups.setdefault(base, []).append((idx, p))

    result: dict[str, Path] = {}
    for base, candidates in groups.items():
        candidates.sort(key=lambda x: x[0])
        result[base] = candidates[0][1]
    return result


# ── Matching ──────────────────────────────────────────────────────────────────

def find_best_match(
    product_text: str,
    index: dict,
    threshold: int,
) -> str | None:
    """
    Fuzzy-match expanded product text against expanded image base stems.
    Returns the matched base stem or None.
    """
    if not index:
        return None

    expanded_product = expand_terms(product_text)
    # Build parallel lists so we can map result index → stem
    stems   = list(index.keys())
    choices = [expand_terms(s) for s in stems]

    result = process.extractOne(
        expanded_product,
        choices,
        scorer=fuzz.token_set_ratio,
    )
    if result and result[1] >= threshold:
        return stems[result[2]]
    return None


def load_csv(path: Path) -> list[dict]:
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)


def match_products(
    csv_in: Path,
    images_dir: Path,
    csv_out: Path,
    threshold: int = 55,
    verbose: bool = False,
) -> tuple[int, int]:
    """
    Match each product to an image using multi-field fuzzy search with
    Spanish↔English term expansion and _N deduplication.
    Falls back to category-level images (e.g. FOTO PASTAS for pastas).
    Returns (matched_count, unmatched_count).
    """
    index = build_deduplicated_index(images_dir)
    rows  = load_csv(csv_in)
    matched = unmatched = 0

    for row in rows:
        base      = row.get('Producto_Base', '') or row.get('Producto', '')
        supplier  = row.get('Proveedor', '')
        categoria = row.get('Categoria', '')
        # Pass 1: base + supplier only — avoids brand confusion
        # (e.g. "Bonomi savoiardy + BONOMI SPA" should not match "galletas loacker"
        # just because both products share the "galletas" categoria).
        best = find_best_match(f"{base} {supplier}", index, threshold)

        # Pass 2: add categoria — needed for brand-generic image names
        # (e.g. "Loacker Classic Napolitaner" → "galletas loacker" only works
        # when "galletas" is included in the search).
        if best is None:
            best = find_best_match(f"{base} {supplier} {categoria}", index, threshold)

        # Pass 3: explicit category-level fallback for image groups
        if best is None:
            fb_base = CATEGORY_FALLBACKS.get(categoria)
            if fb_base and fb_base in index:
                best = fb_base

        if best:
            row['Imagenes'] = str(index[best])
            matched += 1
            if verbose:
                print(f"  OK  {base!r:50s} -> {best!r}")
        else:
            row['Imagenes'] = 'FALSE'
            unmatched += 1
            if verbose:
                print(f"  --  {base!r:50s} (no match)")

    write_csv(rows, csv_out)
    return matched, unmatched


if __name__ == '__main__':
    csv_in     = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('productos_normalizado.csv')
    images_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('extracted_images')
    csv_out    = Path(sys.argv[3]) if len(sys.argv) > 3 else Path('productos_con_imagenes.csv')

    matched, unmatched = match_products(csv_in, images_dir, csv_out, verbose=True)
    total = matched + unmatched
    print(f"\nMatched:   {matched}/{total}")
    print(f"Unmatched: {unmatched}/{total}")
    print(f"Output:    {csv_out}")
