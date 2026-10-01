from __future__ import annotations

from typing import Any
from docx.oxml.ns import qn


def _rgb_text(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).replace("#", "").upper()
    return raw[-6:] if len(raw) >= 6 else None


def is_target_red(color: Any = None, theme_color: Any = None) -> bool:
    """识别标准红、接近红色的 RGB，以及 Word 常见强调色主题红。"""
    rgb = _rgb_text(color)
    if rgb:
        try:
            red, green, blue = (int(rgb[i:i + 2], 16) for i in (0, 2, 4))
            if red >= 150 and red >= green * 1.55 and red >= blue * 1.55:
                return True
        except ValueError:
            pass
    # Theme slot names are not colors; the parser resolves their actual RGB.
    return False


def _font_chain(run):
    yield run.font
    for first in (run.style, run._parent.style):
        seen = set()
        style = first
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            yield style.font
            style = style.base_style


def effective_format(run, theme_colors=None):
    bold, rgb, color_found = None, None, False
    for font in _font_chain(run):
        if bold is None and font.bold is not None:
            bold = font.bold
        color = font.color
        if not color_found and color.type is not None:
            color_found = True
            element = color._element.find(".//" + qn("w:color"))
            if element is not None:
                theme = element.get(qn("w:themeColor"))
                rgb = (theme_colors or {}).get(theme) if theme else None
                rgb = rgb or (str(color.rgb) if color.rgb is not None else None)
                if rgb and len(rgb) == 6:
                    channels = [int(rgb[i:i + 2], 16) for i in (0, 2, 4)]
                    shade, tint = element.get(qn("w:themeShade")), element.get(qn("w:themeTint"))
                    if shade: channels = [round(v * int(shade, 16) / 255) for v in channels]
                    if tint: channels = [round(v + (255 - v) * int(tint, 16) / 255) for v in channels]
                    rgb = "".join(f"{v:02X}" for v in channels)
    return bool(bold), rgb


def run_is_target(run, mode="red_or_bold", theme_colors=None):
    bold, rgb = effective_format(run, theme_colors)
    red = is_target_red(rgb)
    return {"red_or_bold": red or bold, "red_and_bold": red and bold,
            "red": red, "bold": bold, "any": True}[mode]
