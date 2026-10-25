import re
import unicodedata

ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}

_MVT = re.compile(r"^(?P<num>[IVXLC]{1,7}|\d{1,2})[a-e]?\s*[.)]\s*(?P<name>\S.*)?$")
_NUMBERED_PART = re.compile(r"^(?:Var(?:iation|iatio)?|Variation|Aria|No|Nr|Étude|Etude|Prélude|Prelude)\.?\s*(?P<num>\d{1,3})\b", re.I)
_SEPS = [re.compile(r"\s*[:：]\s*|\s+/\s+"), re.compile(r"\s+[-–—]\s+")]
# 只用于"后面紧跟乐章编号"的切分：'…, Op.47, 1. Die…'、'…Op. 15- 2. Largo'
_NUM_SEPS = [re.compile(r",\s+"), re.compile(r"(?<=\S)[-–—]\s+"), re.compile(r"\s+[-–—](?=\S)")]

_CATALOGUE = re.compile(
    r"\b(?P<cat>"
    r"Op(?:us)?\.?\s*posth\.?|"
    r"Op(?:us)?\.?\s*\d+[a-z]?(?:[-/]\d+|\s*,?\s*No\.?\s*\d+[a-z]?)?|"
    r"BWV\s*\d+[a-z]?|K(?:V)?\.?\s*\d+[a-z]?|D\.?\s*\d{1,4}[a-z]?|Hob\.?\s*[IVXL]+[a-z]?\s*:\s*\d+|"
    r"RV\s*\d+|HWV\s*\d+|WoO\s*\d+|S\.?\s*\d{1,3}(?=\b)|L\.?\s*\d{1,3}|Sz\.?\s*\d+|BB\s*\d+|"
    r"WWV\s*\d+[A-Z]?|TrV\s*\d+|FP\s*\d+|M\.?\s*\d{1,3}|B\.?\s*\d{1,3}|Wq\.?\s*\d+|HWV\s*\d+|TWV\s*[\d:]+|"
    r"KK\.?\s*\d+|CT\s*\d+|JW\s*[IVX]+/\d+|Kk\.?\s*\d+"
    r")",
    re.IGNORECASE,
)
# 只写了速度/体裁的乐章标题（CUE 里常见：作品名只出现在第一乐章）
TEMPO = re.compile(
    r"^(?:allegr|adagi|andant|prest|larg|lent|scherz|minuet|menuet|rond|final|vivac|moderat|grave|tempo|"
    r"variat|thema|theme|trio|introdu|marcia|march|sicilian|sarabande|gigue|giga|courante|corrente|allemand|"
    r"gavott|bourr|pr[eé]lud|preludio|fug|aria|romanz|romance|intermezz|cavatin|canzon|recitativ|"
    r"langsam|lebhaft|m[aä]ssig|schnell|ruhig|sehr|rasch|kr[aä]ftig|bewegt|vif|vite|lent|mod[eé]r|anim[eé]|"
    r"tr[eè]s|assez|calme|poco|molto|non\b|un\s+poco|sostenuto|maestoso|grazioso|cantabile|espressivo|"
    r"agitato|appassionato|affettuoso|quasi|l'istesso|attacca|coda|cadenza|kyrie|gloria|credo|sanctus|benedictus|agnus)",
    re.I,
)
_WORKISH = re.compile(r"\b(No\.|Op\.|in [A-G](?:[- ](?:flat|sharp))? (?:major|minor))|[A-G]-?(?:dur|moll)\b", re.I)


def nfc(s: str | None) -> str | None:
    return unicodedata.normalize("NFC", s).strip() if s else s


def roman_to_int(s: str) -> int | None:
    s = s.upper()
    if not s or any(ch not in ROMAN for ch in s):
        return None
    total = 0
    for i, ch in enumerate(s):
        v = ROMAN[ch]
        total += -v if i + 1 < len(s) and ROMAN[s[i + 1]] > v else v
    return total


_PAREN_NUM = re.compile(r"^\((?P<num>\d{1,2})\)\s*(?P<name>\S.*)$")


def parse_movement(text: str) -> tuple[int | None, str] | None:
    text = unicodedata.normalize("NFKC", text)
    pm = _PAREN_NUM.match(text.strip())
    if pm:
        return int(pm.group("num")), pm.group("name").strip()
    m = _MVT.match(text.strip())
    if not m:
        m2 = _NUMBERED_PART.match(text.strip())
        return (int(m2.group("num")), text.strip()) if m2 else None
    num = m.group("num")
    n = int(num) if num.isdigit() else roman_to_int(num)
    return (n, (m.group("name") or "").strip()) if n else None


def strip_composer_prefix(text: str, composer: str | None) -> str:
    if not composer:
        return text
    names = {composer.strip(), composer.split(",")[0].strip(), composer.split()[-1].strip()}
    for name in sorted(names, key=len, reverse=True):
        if name and len(name) > 2:
            m = re.match(rf"^{re.escape(name)}\s*(?:[:：]|\s[-–—])\s*(.+)$", text, re.I)
            if m:
                return m.group(1)
    return text


def split_title(title: str, composer: str | None = None) -> tuple[str, int | None, str] | None:
    """'Piano Sonata No. 32, Op. 111: I. Maestoso' -> (作品, 1, 'Maestoso')。"""
    title = strip_composer_prefix(title.strip(), composer)
    cuts = sorted(
        ((m.start(), m.end(), sep is _SEPS[0]) for sep in _SEPS + _NUM_SEPS for m in sep.finditer(title)),
        key=lambda c: (c[0], -c[1]),
    )
    for start, end, _ in cuts:
        left, right = title[:start].strip(), title[end:].strip()
        if left and right and (mv := parse_movement(right)):
            return left, mv[0], mv[1]
    for start, end, is_colon in reversed(cuts):
        left, right = title[:start].strip(), title[end:].strip()
        if not (left and right and (_CATALOGUE.search(left) or _WORKISH.search(left))):
            continue
        # 'Op. 67: Allegro con brio'；破折号只在右边是速度/体裁术语时才切：'op.18,1 - Allegro con brio'
        if is_colon or TEMPO.match(right):
            return left, None, right
    return None


def extract_catalogue(text: str | None) -> str | None:
    if not text:
        return None
    m = _CATALOGUE.search(text)
    return re.sub(r"\s+", " ", m.group("cat")).strip() if m else None


def work_key(composer: str | None, work: str) -> tuple[str, str]:
    norm = re.sub(r"[\s,.;:：'\"’]+", " ", work.casefold()).strip()
    return ((composer or "").casefold().strip(), norm)


def looks_like_work(text: str) -> bool:
    return bool(_CATALOGUE.search(text) or _WORKISH.search(text)) and not re.search(r"\bCD\s*\d|Disc|Complete|Collection|Recordings|Edition", text, re.I)
