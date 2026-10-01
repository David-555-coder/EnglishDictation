"""Display-only typography; never change article text or target offsets."""
import re


def is_article_heading(style_name):
    name = (style_name or "").strip().casefold()
    return name in {"title", "subtitle", "标题", "副标题"} or bool(
        re.fullmatch(r"(?:heading|标题)\s*[1-9]", name)
    )


def reading_html(html):
    return '<p style="white-space:pre-wrap;line-height:150%;margin:0">' + html.replace('\n', '<br>') + '</p>'
