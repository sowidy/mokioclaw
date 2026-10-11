from functools import lru_cache
from pathlib import Path
from typing import Iterable

from PIL import Image
from rich.text import Text

from mokioclaw.core.paths import find_project_root

FALLBACK_LOGO = [
    "  <      >  ",
    "   /\\__/\\   ",
    "  |  ||  |  ",
    "  |______|  ",
    "   ||  ||   ",
]

def default_logo_path() -> Path:
    return find_project_root() / "assets" / "logo-no-words.png"


def _crop_visible(image: "Image.Image") -> "Image.Image":
    """
    找出图片中实际可见内容，裁掉周围空白，并保留 8 像素边距。
    :param image:
    :return:
    """
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    xs: list[int] = []
    ys: list[int] = []
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a > 16 and (r < 245 or g < 245 or b < 245):
                xs.append(x)
                ys.append(y)
    if not xs or not ys:
        return rgba
    padding = 8
    # min(xs)  最左边的 x 坐标
    # max(xs)  最右边的 x 坐标
    # min(ys)  最上边的 y 坐标
    # max(ys)  最下边的 y 坐标
    # (min(xs), min(ys), max(xs), max(ys)) 表示 Logo 实际内容的矩形范围
    left = max(0, min(xs) - padding)
    top = max(0, min(ys) - padding)
    right = min(width, max(xs) + padding + 1)
    bottom = min(height, max(ys) + padding + 1)
    return rgba.crop((left, top, right, bottom))


def _pad_bottom(image: "Image.Image") -> "Image.Image":
    """
    如果图片高度是奇数，底部补一行透明像素，方便每两行一组处理。
    :param image:
    :return:
    """
    from PIL import Image

    width, height = image.size
    padded = Image.new("RGBA", (width, height + 1), (255, 255, 255, 0))
    padded.paste(image, (0, 0))
    return padded


def _visible(pixel: tuple[int, int, int, int]) -> bool:
    """
    判断像素是否可见。透明度太低或颜色接近白色时，认为不可见。
    :param pixel:
    :return:
    """
    r, g, b, a = pixel
    return a > 16 and (r < 245 or g < 245 or b < 245)


def _pixel_char(upper: tuple[int, int, int, int], lower: tuple[int, int, int, int]) -> str:
    """
    根据上下两个像素决定使用哪个字符
    :param upper:
    :param lower:
    :return:
    """
    upper_visible = _visible(upper)
    lower_visible = _visible(lower)
    if upper_visible and lower_visible:
        return "▀"
    if upper_visible:
        return "▀"
    if lower_visible:
        return "▄"
    return " "

def _rgb(pixel: tuple[int, int, int, int]) -> str:
    return ",".join(str(channel) for channel in pixel[:3])

def _pixel_style(upper: tuple[int, int, int, int], lower: tuple[int, int, int, int]) -> str:
    """
    根据像素颜色生成 Rich 的 RGB 前景色/背景色样式
    :param upper:
    :param lower:
    :return:
    """
    upper_visible = _visible(upper)
    lower_visible = _visible(lower)
    if upper_visible and lower_visible:
        return f"rgb({_rgb(upper)}) on rgb({_rgb(lower)})"
    if upper_visible:
        return f"rgb({_rgb(upper)})"
    if lower_visible:
        return f"rgb({_rgb(lower)})"
    return ""


@lru_cache(maxsize=16)
def _render_png_logo(path: str, *, max_width: int, max_rows: int) -> Text:
    """
    读取、裁剪、缩放 PNG，并转换成终端文本
    :param path:
    :param max_width:
    :param max_rows:
    :return:
    """
    from PIL import Image

    image = Image.open(path).convert("RGBA")
    image = _crop_visible(image)
    image.thumbnail((max_width, max_rows * 2), Image.Resampling.NEAREST)
    width, height = image.size
    if height % 2: # 高度是奇数，底部补一行透明像素，后面会每两行像素合并成一个终端字符
        image = _pad_bottom(image)
        width, height = image.size

    pixels = image.load()
    text = Text()
    for y in range(0, height, 2):
        for x in range(width):
            upper = pixels[x, y]
            lower = pixels[x, y + 1]
            text.append(_pixel_char(upper, lower), style=_pixel_style(upper, lower)) # style 设置颜色
        if y + 2 < height:
            text.append("\n")
    return text


def _render_fallback_logo(lines: Iterable[str] = FALLBACK_LOGO) -> Text:
    """
    渲染备用 ASCII Logo
    :param lines:
    :return:
    """
    text = Text()
    for index, line in enumerate(lines):
        text.append(line, style="bold rgb(255,120,42)")
        if index < len(FALLBACK_LOGO) - 1:
            text.append("\n")
    return text

def render_logo(path: Path | None = None, *, max_width: int = 36, max_rows: int = 12) -> Text:
    """
    对外的主函数，优先渲染 PNG，失败时自动 fallback
    :param path:
    :param max_width:
    :param max_rows:
    :return:
    """
    logo_path = path or default_logo_path()
    try:
        return _render_png_logo(str(logo_path), max_width=max_width, max_rows=max_rows)
    except Exception:
        return _render_fallback_logo()

