import re

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str | None, size: int) -> tuple[int, int] | None | str:
    """返回 (start, end)；无 Range 返回 None；无法满足返回 'unsatisfiable'。只处理第一个区间。"""
    if not header:
        return None
    m = _RANGE.match(header.split(",")[0].strip())
    if not m:
        return None
    a, b = m.groups()
    if a == "" and b == "":
        return None
    if a == "":
        n = int(b)
        if n == 0:
            return "unsatisfiable"
        return max(size - n, 0), size - 1
    start = int(a)
    end = int(b) if b else size - 1
    if start >= size or end < start:
        return "unsatisfiable"
    return start, min(end, size - 1)
