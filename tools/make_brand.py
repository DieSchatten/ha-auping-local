"""Logo-Dateien erzeugen: Brand-Bilder für Home Assistant/HACS und Banner für GitHub.

    python3 tools/make_brand.py

Braucht librsvg + pycairo über PyGObject (System-Python von Zorin/Ubuntu: python3-gi,
gir1.2-rsvg-2.0). Ausgabe:
  custom_components/auping_local/brand/{icon,logo,dark_logo}{,@2x}.png
  docs/banner.png  (README-Kopf und GitHub-Social-Preview, 1280×640)
"""

from __future__ import annotations

from pathlib import Path

import cairo
import gi

gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "custom_components" / "auping_local" / "brand"
DOCS = ROOT / "docs"

NIGHT_1 = "#191C46"
NIGHT_2 = "#2B5C8A"
MOON = "#FFC857"
FRAME = "#FFFFFF"
GHOST = "#86A2DA"

# Seitenansicht eines verstellbaren Lattenrosts: Rückenteil hoch, Knie leicht angewinkelt.
PROFILE = [(112, 236), (206, 326), (290, 326), (346, 292), (414, 326)]
DEPTH = (26, -22)  # Schrägansicht: hinterer Holm
PARTNER = (40, -34)  # zweite Bettseite, parallel versetzt


def _path(points, dx=0.0, dy=0.0) -> str:
    return "M " + " L ".join(f"{x + dx:.1f} {y + dy:.1f}" for x, y in points)


def _rail(points, dx, dy, color, width) -> str:
    return (
        f'<path d="{_path(points, dx, dy)}" stroke="{color}" stroke-width="{width}" '
        'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
    )


def _slatted_frame(color: str) -> str:
    """Lattenrost in Schrägansicht: zwei Holme mit Querlatten."""
    out = [_rail(PROFILE, 0, 0, color, 16), _rail(PROFILE, *DEPTH, color, 16)]
    slats = []
    for (x1, y1), (x2, y2) in zip(PROFILE, PROFILE[1:]):
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        n = max(1, int(length // 34))
        for i in range(n):
            t = (i + 0.5) / n
            x, y = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            slats.append(f"M {x:.1f} {y:.1f} L {x + DEPTH[0]:.1f} {y + DEPTH[1]:.1f}")
    out.append(f'<path d="{" ".join(slats)}" stroke="{color}" stroke-width="10" stroke-linecap="round" fill="none"/>')
    return "".join(out)


def mark(tile: bool = True) -> str:
    """Bildmarke im 512er-Raster: Mond, zwei Lattenroste im Gleichschritt, Motor, Gestell."""
    defs = (
        f'''<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="{NIGHT_1}"/><stop offset="1" stop-color="{NIGHT_2}"/></linearGradient>
          <mask id="moon"><rect width="512" height="512" fill="#fff"/>
          <circle cx="398" cy="114" r="48" fill="#000"/></mask></defs>'''
    )
    parts = [defs]
    if tile:
        parts.append('<rect x="0" y="0" width="512" height="512" rx="124" fill="url(#bg)"/>')
    parts.append(f'<circle cx="372" cy="138" r="54" fill="{MOON}" mask="url(#moon)"/>')
    k, cx, cy = 1.1, 262, 300
    inner = (
        _rail(PROFILE, *PARTNER, GHOST, 22)
        + _slatted_frame(FRAME)
        + f'<rect x="222" y="348" width="62" height="30" rx="10" fill="{MOON}"/>'
        + '<path d="M 128 398 L 416 398 M 156 398 L 156 430 M 388 398 L 388 430 M 253 378 L 253 398" '
        + f'stroke="{FRAME}" stroke-width="18" stroke-linecap="round" fill="none" opacity="0.9"/>'
    )
    parts.append(f'<g transform="translate({256 - cx * k:.1f} {288 - cy * k:.1f}) scale({k})">{inner}</g>')
    return "".join(parts)


def svg_icon() -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">{mark()}</svg>'


def svg_logo(dark: bool) -> str:
    """Querformat: Bildmarke + Schriftzug."""
    text = "#FFFFFF" if dark else "#1B1F45"
    sub = "#AFC0E6" if dark else "#53618A"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1240 512">
      <g>{mark()}</g>
      <text x="560" y="262" font-family="Inter" font-weight="800" font-size="150" fill="{text}"
            letter-spacing="-4">Bett</text>
      <text x="905" y="262" font-family="Inter" font-weight="300" font-size="150" fill="{text}"
            letter-spacing="-4">lokal</text>
      <text x="566" y="350" font-family="Inter" font-weight="500" font-size="54" fill="{sub}">für Auping Connect</text>
    </svg>"""


def svg_banner() -> str:
    """GitHub-Social-Preview 1280×640 (dunkel, ohne Kachel)."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 640">
      <defs><linearGradient id="bgb" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="{NIGHT_1}"/><stop offset="1" stop-color="{NIGHT_2}"/></linearGradient></defs>
      <rect width="1280" height="640" fill="url(#bgb)"/>
      <g transform="translate(16 56)">{mark(tile=False)}</g>
      <text x="580" y="250" font-family="Inter" font-weight="800" font-size="108" fill="#FFFFFF" letter-spacing="-3">Bett lokal</text>
      <text x="584" y="314" font-family="Inter" font-weight="500" font-size="36" fill="#C9D6F2">Auping Connect in Home Assistant</text>
      <text x="584" y="396" font-family="Inter" font-weight="400" font-size="30" fill="#AFC0E6">Ohne Cloud, nur im eigenen WLAN</text>
      <text x="584" y="440" font-family="Inter" font-weight="400" font-size="30" fill="#AFC0E6">Zwei Lattenroste im Gleichschritt</text>
      <text x="584" y="484" font-family="Inter" font-weight="400" font-size="30" fill="#AFC0E6">Kalibrier-Assistent für den Startversatz</text>
    </svg>"""


def render(svg: str, out: Path, width: int, height: int) -> None:
    handle = Rsvg.Handle.new_from_data(svg.encode())
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    ctx = cairo.Context(surface)
    rect = Rsvg.Rectangle()
    rect.x, rect.y, rect.width, rect.height = 0, 0, width, height
    handle.render_document(ctx, rect)
    out.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(out))


def render_trimmed(svg: str, out: Path, width: int, height: int, target_h: int) -> None:
    """Rendern, durchsichtigen Rand abschneiden und auf ``target_h`` Pixel Höhe bringen."""
    scale = 2
    handle = Rsvg.Handle.new_from_data(svg.encode())
    big = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * scale, height * scale)
    ctx = cairo.Context(big)
    rect = Rsvg.Rectangle()
    rect.x, rect.y, rect.width, rect.height = 0, 0, width * scale, height * scale
    handle.render_document(ctx, rect)
    big.flush()
    data, stride = big.get_data(), big.get_stride()
    w, h = big.get_width(), big.get_height()
    xs, ys = [], []
    for y in range(0, h, 2):
        row = data[y * stride : y * stride + w * 4]
        alpha = row[3::4]
        hits = [x for x in range(0, w, 2) if alpha[x] > 8]
        if hits:
            ys.append(y)
            xs.extend((hits[0], hits[-1]))
    x0, x1, y0, y1 = min(xs), max(xs) + 2, min(ys), max(ys) + 2
    k = target_h / (y1 - y0)
    out_w = round((x1 - x0) * k)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, out_w, target_h)
    c = cairo.Context(surface)
    c.scale(k, k)
    c.set_source_surface(big, -x0, -y0)
    c.get_source().set_filter(cairo.FILTER_BEST)
    c.paint()
    out.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(out))


def main() -> None:
    icon = svg_icon()
    render(icon, BRAND / "icon.png", 256, 256)
    render(icon, BRAND / "icon@2x.png", 512, 512)
    for dark, prefix in ((False, ""), (True, "dark_")):
        logo = svg_logo(dark)
        render_trimmed(logo, BRAND / f"{prefix}logo.png", 1240, 512, 256)
        render_trimmed(logo, BRAND / f"{prefix}logo@2x.png", 1240, 512, 512)
    render(svg_banner(), DOCS / "banner.png", 1280, 640)
    (DOCS / "icon.svg").write_text(icon)
    print("Erzeugt:", *sorted(p.name for p in BRAND.glob("*.png")), "docs/banner.png")


if __name__ == "__main__":
    main()
