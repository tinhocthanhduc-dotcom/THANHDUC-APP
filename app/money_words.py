"""Đọc số nguyên đồng bằng chữ Việt; dùng số nguyên để giữ đủ giá trị BigInteger."""
_DIGITS = ('không', 'một', 'hai', 'ba', 'bốn', 'năm', 'sáu', 'bảy', 'tám', 'chín')
_SCALES = ('', 'nghìn', 'triệu', 'tỷ', 'nghìn tỷ', 'triệu tỷ', 'tỷ tỷ')


def _triple(value, full=False):
    hundred, rest = divmod(value, 100)
    ten, one = divmod(rest, 10)
    words = []
    if hundred or full:
        words.extend((_DIGITS[hundred], 'trăm'))
    if ten > 1:
        words.extend((_DIGITS[ten], 'mươi'))
    elif ten == 1:
        words.append('mười')
    elif one and (hundred or full):
        words.append('lẻ')
    if one:
        words.append('mốt' if one == 1 and ten > 1 else 'lăm' if one == 5 and ten else _DIGITS[one])
    return words


def money_words(value: int) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 9_223_372_036_854_775_807:
        raise ValueError('Số tiền phải là số nguyên đồng không âm trong giới hạn BigInteger.')
    if value == 0:
        return 'Không đồng'
    groups = []
    while value:
        value, part = divmod(value, 1000)
        groups.append(part)
    words = []
    for index in range(len(groups) - 1, -1, -1):
        if not groups[index]:
            continue
        words.extend(_triple(groups[index], full=bool(words)))
        if _SCALES[index]:
            words.append(_SCALES[index])
    return (' '.join(words) + ' đồng').capitalize()
