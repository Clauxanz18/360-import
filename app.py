"""
360 Import — Catalog Review App
Streamlit app for manually reviewing and correcting image-product matches.
"""
from __future__ import annotations

import base64
import csv
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image
import streamlit as st

from extract_images import should_ignore_extracted_png, should_skip_truncated_xls_jpeg

# Trust local catalog assets; avoid PIL DecompressionBombWarning on very large PNGs.
Image.MAX_IMAGE_PIXELS = 300_000_000

# ── Config ────────────────────────────────────────────────────────────────────

CSV_SOURCE   = Path("productos_con_imagenes.csv")
CSV_EXPORT   = Path("productos_final.csv")
IMAGES_DIR   = Path("extracted_images")
STATE_FILE   = Path("review_state.json")
PDF_OUTPUT   = Path("catalogo_360import.pdf")
TEMPLATE     = Path("catalog_template.html")
IMAGE_EXTS   = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
LOGO_PATH    = Path("assets/images/LOGO 360 IMPORT.PNG")

st.set_page_config(
    page_title="360 Import — Catálogo",
    page_icon="🇮🇹",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Data helpers ──────────────────────────────────────────────────────────────

@st.cache_data
def load_products() -> list[dict]:
    with open(CSV_SOURCE, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@st.cache_data
def get_all_images() -> list[Path]:
    paths = sorted(
        [
            p for p in IMAGES_DIR.iterdir()
            if p.is_file()
            and p.suffix.lower() in IMAGE_EXTS
            and not should_ignore_extracted_png(p)
            and not should_skip_truncated_xls_jpeg(p)
        ],
        key=lambda p: p.name.lower(),
    )
    return paths


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def img_b64(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "jpg":
        suffix = "jpeg"
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/{suffix};base64,{data}"


def img_html(path: Path, width: str = "100%", height: str = "220px") -> str:
    return (
        f'<img src="{img_b64(path)}" '
        f'style="width:{width};height:{height};object-fit:contain;border-radius:6px;" />'
    )


def export_final_csv(products: list[dict], state: dict) -> None:
    rows = []
    for i, row in enumerate(products):
        r = dict(row)
        entry = state.get(str(i), {})
        if "image" in entry:
            r["Imagenes"] = entry["image"]
        rows.append(r)
    if rows:
        fieldnames = list(rows[0].keys())
        with open(CSV_EXPORT, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(rows)


def get_product_image(idx: int, products: list[dict], state: dict) -> str | None:
    """Return the current image path for a product (state overrides CSV)."""
    entry = state.get(str(idx), {})
    if "image" in entry:
        return entry["image"]
    return products[idx].get("Imagenes", "FALSE")


def status_of(idx: int, state: dict) -> str:
    """'approved' | 'no_image' | 'pending'"""
    return state.get(str(idx), {}).get("status", "pending")


# ── Sidebar ───────────────────────────────────────────────────────────────────

def render_sidebar(products: list[dict], state: dict) -> tuple[list[int], str]:
    with st.sidebar:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=160)
        st.title("360 Import")
        st.caption("Revisión de Catálogo")
        st.divider()

        # Progress
        total = len(products)
        approved  = sum(1 for i in range(total) if status_of(i, state) == "approved")
        no_img    = sum(1 for i in range(total) if status_of(i, state) == "no_image")
        pending   = total - approved - no_img
        matched   = sum(1 for p in products if p.get("Imagenes", "FALSE") not in ("FALSE", ""))

        st.markdown(f"**Progreso:** {approved + no_img}/{total} revisados")
        st.progress((approved + no_img) / total if total else 0)
        col1, col2, col3 = st.columns(3)
        col1.metric("✅ Aprobados", approved)
        col2.metric("❌ Sin imagen", no_img)
        col3.metric("⏳ Pendientes", pending)
        st.caption(f"Fuzzy match inicial: {matched}/{total}")
        st.divider()

        # Filters
        st.subheader("Filtros")
        categories = sorted({p.get("Categoria", "") for p in products if p.get("Categoria")})
        cat_filter = st.selectbox("Categoría", ["Todas"] + categories)

        status_filter = st.radio(
            "Estado",
            ["Todos", "Pendientes", "Aprobados", "Sin imagen"],
            horizontal=True,
        )
        search = st.text_input("Buscar producto", placeholder="nombre, proveedor…")
        st.divider()

        # Actions
        st.subheader("Acciones")
        if st.button("📥 Exportar CSV final", use_container_width=True):
            export_final_csv(products, state)
            st.success(f"Exportado: {CSV_EXPORT}")

        if st.button("📄 Generar PDF", use_container_width=True, type="primary"):
            export_final_csv(products, state)
            with st.spinner("Generando PDF…"):
                result = subprocess.run(
                    [sys.executable, "generate_pdf.py", str(CSV_EXPORT), str(TEMPLATE), str(PDF_OUTPUT)],
                    capture_output=True, text=True,
                )
            if result.returncode == 0:
                st.success(f"PDF generado: {PDF_OUTPUT}")
            else:
                st.error(f"Error: {result.stderr[-500:]}")

    # Build filtered index list
    filtered: list[int] = []
    for i, p in enumerate(products):
        if cat_filter != "Todas" and p.get("Categoria", "") != cat_filter:
            continue
        st_val = status_of(i, state)
        if status_filter == "Pendientes" and st_val != "pending":
            continue
        if status_filter == "Aprobados" and st_val != "approved":
            continue
        if status_filter == "Sin imagen" and st_val != "no_image":
            continue
        if search:
            term = search.lower()
            haystack = " ".join([
                p.get("Producto", ""), p.get("Producto_Base", ""),
                p.get("Proveedor", ""), p.get("Categoria", ""),
            ]).lower()
            if term not in haystack:
                continue
        filtered.append(i)

    return filtered, cat_filter


# ── Image picker ──────────────────────────────────────────────────────────────

def render_image_picker(product_idx: int, state: dict) -> None:
    all_images = get_all_images()
    st.markdown("#### Seleccionar imagen")

    search = st.text_input("Buscar imagen", key=f"img_search_{product_idx}", placeholder="nombre de archivo…")
    filtered = [p for p in all_images if not search or search.lower() in p.name.lower()]

    if not filtered:
        st.info("Sin resultados.")
        return

    cols_per_row = 4
    for row_start in range(0, len(filtered), cols_per_row):
        cols = st.columns(cols_per_row)
        for col_idx, img_path in enumerate(filtered[row_start: row_start + cols_per_row]):
            with cols[col_idx]:
                st.markdown(img_html(img_path, height="120px"), unsafe_allow_html=True)
                st.caption(img_path.name[:28])
                if st.button("Seleccionar", key=f"pick_{product_idx}_{img_path.name}"):
                    entry = state.setdefault(str(product_idx), {})
                    entry["image"] = str(img_path)
                    entry["status"] = "approved"
                    save_state(state)
                    st.session_state.picking = False
                    st.rerun()


# ── Product card ──────────────────────────────────────────────────────────────

def render_product_card(
    product_idx: int,
    products: list[dict],
    state: dict,
    filtered_indices: list[int],
) -> None:
    p = products[product_idx]
    st_val = status_of(product_idx, state)
    img_path_str = get_product_image(product_idx, products, state)
    img_path = Path(img_path_str) if img_path_str and img_path_str != "FALSE" else None
    img_valid = bool(
        img_path
        and img_path.exists()
        and not should_ignore_extracted_png(img_path)
        and not should_skip_truncated_xls_jpeg(img_path)
    )

    # Navigation
    pos = filtered_indices.index(product_idx) if product_idx in filtered_indices else 0
    nav_left, nav_info, nav_right = st.columns([1, 4, 1])
    with nav_left:
        if st.button("← Anterior", disabled=pos == 0, use_container_width=True):
            st.session_state.current_idx = filtered_indices[pos - 1]
            st.session_state.picking = False
            st.rerun()
    with nav_info:
        st.markdown(
            f"<div style='text-align:center;padding-top:6px'>"
            f"Producto <b>{pos + 1}</b> de <b>{len(filtered_indices)}</b></div>",
            unsafe_allow_html=True,
        )
    with nav_right:
        if st.button("Siguiente →", disabled=pos >= len(filtered_indices) - 1, use_container_width=True):
            st.session_state.current_idx = filtered_indices[pos + 1]
            st.session_state.picking = False
            st.rerun()

    st.divider()

    # Product info + image
    info_col, img_col = st.columns([3, 2])

    with info_col:
        STATUS_BADGE = {
            "approved": "🟢 Aprobado",
            "no_image": "🔴 Sin imagen",
            "pending":  "🟡 Pendiente",
        }
        st.markdown(f"**Estado:** {STATUS_BADGE[st_val]}")
        st.markdown(f"## {p.get('Producto', '')}")
        st.markdown(f"**Base:** {p.get('Producto_Base', '—')}")
        st.markdown(f"**Presentación:** {p.get('Presentacion', '—')}  |  **Unidades/Caja:** {p.get('Unidades_por_Caja', '—')}")
        st.markdown(f"**Categoría:** `{p.get('Categoria', '—')}`")
        st.markdown(f"**Proveedor:** {p.get('Proveedor', '—')}")

        desc = p.get("Descripción", "")
        if desc:
            st.markdown(f"**Descripción:** {desc}")

        st.divider()

        # Action buttons
        btn_col1, btn_col2, btn_col3 = st.columns(3)
        with btn_col1:
            if st.button("✅ Aprobar", use_container_width=True, type="primary", key=f"approve_{product_idx}"):
                entry = state.setdefault(str(product_idx), {})
                entry["status"] = "approved"
                if "image" not in entry:
                    entry["image"] = (
                        img_path_str if img_valid else "FALSE"
                    )
                save_state(state)
                # Auto-advance to next
                if pos < len(filtered_indices) - 1:
                    st.session_state.current_idx = filtered_indices[pos + 1]
                    st.session_state.picking = False
                st.rerun()
        with btn_col2:
            toggle_label = "🔽 Cerrar galería" if st.session_state.get("picking") else "🔄 Cambiar imagen"
            if st.button(toggle_label, use_container_width=True, key=f"change_{product_idx}"):
                st.session_state.picking = not st.session_state.get("picking", False)
                st.rerun()
        with btn_col3:
            if st.button("❌ Sin imagen", use_container_width=True, key=f"noimag_{product_idx}"):
                entry = state.setdefault(str(product_idx), {})
                entry["image"] = "FALSE"
                entry["status"] = "no_image"
                save_state(state)
                if pos < len(filtered_indices) - 1:
                    st.session_state.current_idx = filtered_indices[pos + 1]
                    st.session_state.picking = False
                st.rerun()

        if img_path and img_path.exists():
            st.caption(f"📁 `{img_path.name}`")
        if img_path and img_path.exists() and not img_valid:
            st.caption(
                "Imagen excluida (logo ~33 KB o JPEG .xls truncado); elige otra en la galería."
            )

    with img_col:
        st.markdown("**Imagen actual**")
        if img_valid:
            st.markdown(img_html(img_path, height="320px"), unsafe_allow_html=True)
        else:
            st.markdown(
                "<div style='height:320px;display:flex;align-items:center;justify-content:center;"
                "background:#f5f5f5;border-radius:8px;border:2px dashed #ccc;'>"
                "<span style='color:#999;font-size:48px;'>📷</span></div>",
                unsafe_allow_html=True,
            )
            st.caption("Sin imagen asignada")

    # Image picker gallery
    if st.session_state.get("picking"):
        st.divider()
        render_image_picker(product_idx, state)


# ── Product list panel ────────────────────────────────────────────────────────

def render_product_list(filtered_indices: list[int], products: list[dict], state: dict) -> None:
    STATUS_ICON = {"approved": "🟢", "no_image": "🔴", "pending": "🟡"}
    current = st.session_state.get("current_idx", filtered_indices[0] if filtered_indices else 0)

    st.markdown(f"**{len(filtered_indices)} productos**")
    for i in filtered_indices:
        p = products[i]
        st_val = status_of(i, state)
        icon = STATUS_ICON[st_val]
        label = f"{icon} {p.get('Producto_Base', p.get('Producto', ''))[:45]}"
        is_active = i == current
        btn_type = "primary" if is_active else "secondary"
        if st.button(label, key=f"nav_{i}", use_container_width=True, type=btn_type):
            st.session_state.current_idx = i
            st.session_state.picking = False
            st.rerun()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    products = load_products()
    state = load_state()

    if "current_idx" not in st.session_state:
        st.session_state.current_idx = 0
    if "picking" not in st.session_state:
        st.session_state.picking = False

    filtered_indices, _ = render_sidebar(products, state)

    if not filtered_indices:
        st.info("No hay productos que coincidan con los filtros seleccionados.")
        return

    # Clamp current index to filtered list
    if st.session_state.current_idx not in filtered_indices:
        st.session_state.current_idx = filtered_indices[0]

    list_col, card_col = st.columns([1, 3])

    with list_col:
        render_product_list(filtered_indices, products, state)

    with card_col:
        render_product_card(st.session_state.current_idx, products, state, filtered_indices)


if __name__ == "__main__":
    main()
