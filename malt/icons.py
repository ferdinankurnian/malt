"""Project icon resolution: auto favicon scan, custom upload, letter avatar."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo
import cairo

ICONS_DIR = Path.home() / ".local" / "share" / "malt" / "icons"

AUTO_CANDIDATES = [
    "favicon.ico",
    "favicon.png",
    "favicon.svg",
    "public/favicon.ico",
    "public/favicon.png",
    "public/favicon.svg",
    "static/favicon.ico",
    "static/favicon.png",
    "assets/favicon.ico",
    "assets/favicon.png",
    "app/favicon.ico",
    "src/favicon.ico",
    "src/assets/favicon.ico",
]


def icons_dir() -> Path:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    return ICONS_DIR


def find_auto_icon(root: str | Path) -> Path | None:
    root_p = Path(root)
    if not root_p.is_dir():
        return None
    for rel in AUTO_CANDIDATES:
        p = root_p / rel
        if p.is_file():
            return p
    return None


def custom_icon_path(project_id: str, ext: str) -> Path:
    return icons_dir() / f"{project_id}{ext}"


def delete_custom_icons(project_id: str) -> None:
    d = icons_dir()
    for p in d.glob(f"{project_id}.*"):
        try:
            p.unlink()
        except OSError:
            pass


def save_custom_icon(project_id: str, source: str | Path) -> str:
    """Copy source into icons dir; return relative filename (e.g. 'abc.png')."""
    src = Path(source)
    ext = src.suffix.lower() or ".png"
    if ext not in (".png", ".svg", ".ico", ".webp", ".jpg", ".jpeg"):
        ext = ".png"
    delete_custom_icons(project_id)
    dest = custom_icon_path(project_id, ext)
    shutil.copy2(src, dest)
    return dest.name


def resolve_icon_file(
    root_path: str,
    icon_mode: str = "auto",
    icon_path: str | None = None,
) -> Path | None:
    mode = icon_mode or "auto"
    if mode == "none":
        return None
    if mode == "custom" and icon_path:
        p = icons_dir() / icon_path
        if p.is_file():
            return p
        return None
    # auto
    return find_auto_icon(root_path)


_VIVID_PALETTE: list[tuple[float, float, float]] = [
    (0.08, 0.42, 0.72),
    (0.43, 0.18, 0.72),
    (0.04, 0.52, 0.48),
    (0.72, 0.18, 0.24),
    (0.12, 0.52, 0.28),
    (0.72, 0.42, 0.08),
    (0.25, 0.28, 0.72),
    (0.05, 0.48, 0.62),
    (0.62, 0.16, 0.46),
    (0.12, 0.38, 0.68),
    (0.58, 0.28, 0.10),
    (0.18, 0.34, 0.58),
]


def _letter_and_color(name: str, project_id: str) -> tuple[str, tuple[float, float, float]]:
    ch = "?"
    for c in name or "":
        if c.isalnum():
            ch = c.upper()
            break
    h = int(hashlib.md5((project_id or name or "x").encode()).hexdigest()[:8], 16)
    r, g, b = _VIVID_PALETTE[h % len(_VIVID_PALETTE)]
    return ch, (r, g, b)


def make_letter_paintable(
    name: str,
    project_id: str,
    size: int = 24,
) -> Gdk.Paintable:
    """Render a letter avatar as a Gdk.Texture."""
    letter, (r, g, b) = _letter_and_color(name, project_id)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    # rounded rect background
    radius = max(size * 0.22, 2)
    cr.new_sub_path()
    cr.arc(size - radius, radius, radius, -1.5708, 0)
    cr.arc(size - radius, size - radius, radius, 0, 1.5708)
    cr.arc(radius, size - radius, radius, 1.5708, 3.14159)
    cr.arc(radius, radius, radius, 3.14159, 4.71239)
    cr.close_path()
    cr.set_source_rgb(r, g, b)
    cr.fill()

    # letter
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription()
    font.set_family("Sans")
    font.set_weight(Pango.Weight.BOLD)
    font.set_size(int(size * 0.55 * Pango.SCALE))
    layout.set_font_description(font)
    layout.set_text(letter, -1)
    tw, th = layout.get_pixel_size()
    cr.set_source_rgb(1, 1, 1)
    cr.move_to((size - tw) / 2, (size - th) / 2)
    PangoCairo.show_layout(cr, layout)

    surface.flush()
    # Convert Cairo ARGB32 (native endian BGRA on LE) → RGBA for GdkPixbuf
    stride = surface.get_stride()
    src = bytes(surface.get_data())
    rgba = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            i = y * stride + x * 4
            o = (y * size + x) * 4
            b_, g_, r_, a_ = src[i], src[i + 1], src[i + 2], src[i + 3]
            rgba[o] = r_
            rgba[o + 1] = g_
            rgba[o + 2] = b_
            rgba[o + 3] = a_
    pixbuf = GdkPixbuf.Pixbuf.new_from_data(
        bytes(rgba),
        GdkPixbuf.Colorspace.RGB,
        True,
        8,
        size,
        size,
        size * 4,
        None,
        None,
    )
    return Gdk.Texture.new_for_pixbuf(pixbuf)


def load_file_paintable(path: Path, size: int = 24) -> Gdk.Paintable | None:
    try:
        # Prefer pixbuf loader for raster; handles png/jpg/ico/webp
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(path), size, size, True
        )
        if pixbuf is None:
            return None
        return Gdk.Texture.new_for_pixbuf(pixbuf)
    except GLib.Error:
        try:
            # Fallback: Gdk.Texture direct (may work for some formats)
            return Gdk.Texture.new_from_filename(str(path))
        except Exception:
            return None


def get_project_paintable(
    *,
    name: str,
    project_id: str,
    root_path: str,
    icon_mode: str = "auto",
    icon_path: str | None = None,
    size: int = 24,
) -> Gdk.Paintable:
    path = resolve_icon_file(root_path, icon_mode, icon_path)
    if path is not None:
        paintable = load_file_paintable(path, size)
        if paintable is not None:
            return paintable
    return make_letter_paintable(name, project_id, size)


def apply_to_image(
    image: Gtk.Image,
    *,
    name: str,
    project_id: str,
    root_path: str,
    icon_mode: str = "auto",
    icon_path: str | None = None,
    size: int = 24,
) -> None:
    paintable = get_project_paintable(
        name=name,
        project_id=project_id,
        root_path=root_path,
        icon_mode=icon_mode,
        icon_path=icon_path,
        size=size,
    )
    image.set_from_paintable(paintable)
    image.set_pixel_size(size)
