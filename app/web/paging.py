"""Phân trang dùng chung cho danh sách dài (nguyên tắc phân trang 23/10/2026)."""
from urllib.parse import urlencode

SIZES = (25, 50, 100)
DEFAULT_SIZE = 25


def size_of(raw) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SIZE
    return value if value in SIZES else DEFAULT_SIZE


def page_of(raw) -> int:
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def build(raw_page, raw_size, total: int, query: dict, key: str = 'trang') -> dict:
    """Trang quá lớn thì về trang cuối. query: bộ lọc hiện tại, không gồm trang.
    key: tên tham số trang, để một màn có nhiều khu phân trang riêng (vd. trang-tho)."""
    size = size_of(raw_size)
    last = max(1, -(-total // size))
    page = min(page_of(raw_page), last)
    keep = {k: v for k, v in query.items() if v not in ('', None) and k not in (key, 'phan', 'so')}
    if size != DEFAULT_SIZE:
        keep['so'] = size

    def href(n):
        return '?' + urlencode({**keep, **({key: n} if n > 1 else {})})

    numbers = sorted({1, last, *range(max(1, page - 1), min(last, page + 1) + 1)})
    items, previous = [], 0
    for n in numbers:
        if n - previous > 1:
            items.append(None)
        items.append(n)
        previous = n
    return dict(page=page, size=size, total=total, last=last, offset=(page - 1) * size,
                start=(page - 1) * size + 1 if total else 0, end=min(page * size, total),
                numbers=items, href=href, sizes=SIZES)


def slice_rows(rows: list, raw_page, raw_size, query: dict):
    """Danh sách đã lọc đủ trong bộ nhớ: số đếm và tổng tính trên cả list, chỉ cắt phần hiển thị."""
    page = build(raw_page, raw_size, len(rows), query)
    return page, rows[page['offset']:page['offset'] + page['size']]


def linker(path: str, query: dict):
    """link(**đổi) -> đường dẫn giữ bộ lọc hiện tại, bỏ ô rỗng; đổi lọc thì về trang 1."""
    def link(**changes):
        merged = {k: v for k, v in {**query, **changes}.items() if v not in ('', None, False) and k not in ('trang', 'phan')}
        return path + ('?' + urlencode(merged) if merged else '')
    return link
