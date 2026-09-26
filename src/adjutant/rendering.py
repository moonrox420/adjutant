"""Render editable scene text over a separately stored image with measured layout validation."""

import base64
import io
import warnings
from dataclasses import dataclass
from html import escape

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from adjutant.errors import DomainError

SIZES = {
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
}


@dataclass(frozen=True)
class RenderedAd:
    png: bytes
    svg: bytes
    scene: dict
    width: int
    height: int


def lines_for(text: str, size: int, width: int) -> list[str]:
    """Wrap at words using the renderer's actual font metrics; reject unbreakable overflow."""
    font = ImageFont.load_default(size=size)
    lines: list[str] = []
    current = ""
    for word in text.split():
        if font.getlength(word) > width:
            raise DomainError(
                "TextOverflow", "An unbroken word exceeds the text safe area.", 422
            )
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def render_ad(document: dict, background: bytes, aspect_ratio: str) -> RenderedAd:
    """Produce PNG and selectable-text SVG without changing the source image or copy."""
    if aspect_ratio not in SIZES:
        raise DomainError("InvalidAspectRatio", "Choose 1:1, 4:5, 9:16, or 16:9.", 422)
    width, height = SIZES[aspect_ratio]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(background)) as source:
                if source.width * source.height > 25_000_000:
                    raise ValueError("Image exceeds pixel limit")
                source.load()
                source = ImageOps.exif_transpose(source).convert("RGB")
                image_height = int(height * 0.51)
                visual = ImageOps.contain(source, (width, image_height))
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise DomainError(
            "InvalidImage", "The stored image cannot be safely rendered.", 422
        ) from exc
    canvas = Image.new("RGB", (width, height), "#ffffff")
    image_x = (width - visual.width) // 2
    image_y = (image_height - visual.height) // 2
    canvas.paste(visual, (image_x, image_y))
    draw = ImageDraw.Draw(canvas)
    margin = 64
    text_width = width - margin * 2
    fields = [
        ("brand", document["brand_name"], 30),
        ("headline", document["meta"]["headline"], 54),
        ("description", document["meta"]["description"], 32),
        ("cta", document["meta"]["cta"], 32),
    ]
    y = image_height + 36
    text_layers = []
    for role, text, size in fields:
        lines = lines_for(text, size, text_width)
        line_height = int(size * 1.35)
        box_height = len(lines) * line_height
        if not lines or y + box_height > height - margin:
            raise DomainError(
                "TextOverflow", f"The {role} exceeds the {aspect_ratio} safe area.", 422
            )
        layer = {
            "type": "text",
            "role": role,
            "content": text,
            "lines": lines,
            "x": margin,
            "y": y,
            "font_size": size,
            "line_height": line_height,
            "width": text_width,
            "height": box_height,
            "color": "#142b22",
        }
        text_layers.append(layer)
        font = ImageFont.load_default(size=size)
        for index, line in enumerate(lines):
            draw.text(
                (margin, y + index * line_height),
                line,
                font=font,
                fill="#142b22",
                anchor="lt",
            )
        y += box_height + 18
    encoded_image = io.BytesIO()
    visual.save(encoded_image, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(encoded_image.getvalue()).decode()
    fragments = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/>',
        f'<image x="{image_x}" y="{image_y}" width="{visual.width}" '
        f'height="{visual.height}" href="{uri}"/>',
    ]
    for layer in text_layers:
        for index, line in enumerate(layer["lines"]):
            measured = ImageFont.load_default(size=layer["font_size"]).getlength(line)
            fragments.append(
                f'<text x="{layer["x"]}" y="{layer["y"] + index * layer["line_height"]}" '
                f'dominant-baseline="hanging" font-family="sans-serif" '
                f'font-size="{layer["font_size"]}" fill="{layer["color"]}" '
                f'textLength="{measured:.2f}" lengthAdjust="spacingAndGlyphs">{escape(line)}</text>'
            )
    fragments.append("</svg>")
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    luminance = sum(
        weight
        * (
            ((value / 255 + 0.055) / 1.055) ** 2.4
            if value / 255 > 0.04045
            else value / 255 / 12.92
        )
        for value, weight in zip((20, 43, 34), (0.2126, 0.7152, 0.0722), strict=True)
    )
    return RenderedAd(
        output.getvalue(),
        "".join(fragments).encode(),
        {
            "version": 2,
            "aspect_ratio": aspect_ratio,
            "width": width,
            "height": height,
            "text_contrast_ratio": round(1.05 / (luminance + 0.05), 2),
            "safe_margin": margin,
            "layers": [{"type": "image", "role": "background"}, *text_layers],
        },
        width,
        height,
    )
