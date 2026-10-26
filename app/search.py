"""Luật tìm chung của app: không dấu, nhiều chữ theo thứ tự bất kỳ, viết tắt thường gặp, xếp hạng.

Ô tìm nào cũng gọi vào đây để gõ "107 hp" hay "cty thanh" ra cùng một kiểu ở mọi màn.
"""
from sqlalchemy import and_, case, func, or_

from .text import fold

# Viết tắt hay gõ: mỗi chữ khớp một trong các cách viết.
ABBR = {"cty": ("cty", "cong ty")}


def like_pattern(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


def words(q: str | None) -> list[tuple[str, ...]]:
    """Mỗi chữ gõ thành bộ cách viết được chấp nhận: 'cty thanh' -> [('cty','cong ty'), ('thanh',)]."""
    return [ABBR.get(w, (w,)) for w in fold(q).split()]


def text_matches(q: str | None, text: str | None) -> bool:
    """Lọc trong Python: mọi chữ gõ đều có trong text (không dấu, thứ tự bất kỳ)."""
    hay = fold(text)
    return all(any(alt in hay for alt in alts) for alts in words(q))


def like_all(column, q: str | None) -> list:
    """Điều kiện SQL trên cột đã bỏ dấu (search_text): mọi chữ gõ đều có trong cột."""
    return [or_(*(column.like(like_pattern(alt)) for alt in alts)) for alts in words(q)]


def rank(code, text, q: str | None):
    """Hạng 0 mã trùng đúng, 1 mã bắt đầu bằng chữ gõ, 2 mọi chữ gõ đều là đầu một từ, 3 có chữ chỉ chứa ở giữa."""
    needle = fold(q)
    code = func.lower(code)
    word_starts = and_(*(or_(*(cond for alt in alts for cond in (text.like(like_pattern(alt)[1:]), text.like(like_pattern(" " + alt)))))
                         for alts in words(q)))
    return case((code == needle, 0), (code.like(like_pattern(needle)[1:]), 1), (word_starts, 2), else_=3)
