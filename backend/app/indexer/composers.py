"""常见作曲家名录：用于统一写法（'Mozart, Wolfgang Amadeus' / 'W. A. Mozart' / '莫扎特'）和从标题、专辑名中识别作曲家。"""

import re
import unicodedata

# 规范名: [别名…]。别名中的姓氏会在自由文本中按整词匹配；以 "=" 开头的别名只在标签值完全相等时使用。
COMPOSERS: dict[str, list[str]] = {
    "Johann Sebastian Bach": ["J. S. Bach", "JS Bach", "J.S.Bach", "Bach", "巴赫"],
    "Carl Philipp Emanuel Bach": ["C. P. E. Bach", "CPE Bach", "C.P.E. Bach"],
    "Ludwig van Beethoven": ["Beethoven", "贝多芬", "貝多芬"],
    "Wolfgang Amadeus Mozart": ["W. A. Mozart", "WA Mozart", "Mozart", "莫扎特", "莫札特"],
    "Joseph Haydn": ["Franz Joseph Haydn", "J. Haydn", "Haydn", "海顿", "海頓"],
    "Michael Haydn": ["M. Haydn"],
    "Franz Schubert": ["Schubert", "舒伯特"],
    "Robert Schumann": ["R. Schumann", "Schumann", "舒曼"],
    "Clara Schumann": ["Clara Wieck", "克拉拉·舒曼"],
    "Frédéric Chopin": ["Fryderyk Chopin", "Chopin", "肖邦", "蕭邦"],
    "Franz Liszt": ["Liszt", "李斯特"],
    "Felix Mendelssohn": ["Felix Mendelssohn Bartholdy", "Mendelssohn", "门德尔松", "孟德尔颂"],
    "Fanny Mendelssohn": ["Fanny Hensel"],
    "Johannes Brahms": ["Brahms", "勃拉姆斯", "布拉姆斯"],
    "Pyotr Ilyich Tchaikovsky": ["Пётр Ильич Чайковский", "Чайковский", "Tchaikovsky", "Tchaikowsky", "Tschaikowsky", "Chaikovsky", "柴可夫斯基"],
    "Antonín Dvořák": ["Dvořák", "Dvorak", "德沃夏克", "德沃夏克"],
    "Bedřich Smetana": ["Smetana", "斯美塔那"],
    "Leoš Janáček": ["Janáček", "Janacek", "雅纳切克"],
    "Bohuslav Martinů": ["Martinů", "Martinu"],
    "Edvard Grieg": ["Grieg", "Greg", "格里格"],
    "Jean Sibelius": ["Sibelius", "西贝柳斯"],
    "Carl Nielsen": ["Nielsen"],
    "Claude Debussy": ["Debussy", "德彪西"],
    "Maurice Ravel": ["Ravel", "拉威尔"],
    "Gabriel Fauré": ["Fauré", "Faure", "福雷"],
    "Camille Saint-Saëns": ["Saint-Saëns", "Saint-Saens", "Saint Saens", "圣桑"],
    "César Franck": ["Franck", "弗朗克"],
    "Erik Satie": ["Satie", "萨蒂"],
    "Georges Bizet": ["Bizet", "比才"],
    "Hector Berlioz": ["Berlioz", "柏辽兹"],
    "Charles Gounod": ["Gounod", "古诺"],
    "Jules Massenet": ["Massenet", "马斯奈"],
    "Emmanuel Chabrier": ["Chabrier"],
    "Ernest Chausson": ["Chausson"],
    "Vincent d'Indy": ["d'Indy"],
    "Francis Poulenc": ["Poulenc", "普朗克"],
    "Darius Milhaud": ["Milhaud"],
    "Arthur Honegger": ["Honegger"],
    "Olivier Messiaen": ["Messiaen", "梅西安"],
    "Jean-Philippe Rameau": ["Rameau", "拉莫"],
    "François Couperin": ["Couperin", "库普兰"],
    "Jean-Baptiste Lully": ["Lully", "吕利"],
    "Marc-Antoine Charpentier": ["Charpentier"],
    "Antonio Vivaldi": ["Vivaldi", "维瓦尔第", "韦瓦第"],
    "Arcangelo Corelli": ["Corelli", "科雷利"],
    "Domenico Scarlatti": ["D. Scarlatti", "Scarlatti", "斯卡拉蒂"],
    "Alessandro Scarlatti": ["A. Scarlatti"],
    "Giovanni Battista Pergolesi": ["Pergolesi", "佩尔戈莱西"],
    "Tomaso Albinoni": ["Albinoni", "阿尔比诺尼"],
    "Giuseppe Tartini": ["Tartini", "塔尔蒂尼"],
    "Luigi Boccherini": ["Boccherini", "博凯里尼", "鲍凯里尼"],
    "Niccolò Paganini": ["Paganini", "帕格尼尼"],
    "Gioachino Rossini": ["Rossini", "罗西尼"],
    "Gaetano Donizetti": ["Donizetti", "多尼采蒂"],
    "Vincenzo Bellini": ["Bellini", "贝里尼"],
    "Giuseppe Verdi": ["Verdi", "威尔第"],
    "Giacomo Puccini": ["Puccini", "普契尼"],
    "Pietro Mascagni": ["Mascagni"],
    "Ruggero Leoncavallo": ["Leoncavallo"],
    "Ottorino Respighi": ["Respighi", "雷斯庇基"],
    "Claudio Monteverdi": ["Monteverdi", "蒙特威尔第"],
    "Giovanni Pierluigi da Palestrina": ["Palestrina", "帕莱斯特里纳"],
    "George Frideric Handel": ["Georg Friedrich Händel", "Handel", "Händel", "Haendel", "亨德尔"],
    "Georg Philipp Telemann": ["Telemann", "泰勒曼"],
    "Henry Purcell": ["Purcell", "珀塞尔"],
    "Dieterich Buxtehude": ["Buxtehude", "布克斯特胡德"],
    "Heinrich Schütz": ["Schütz", "Schutz"],
    "Johann Pachelbel": ["Pachelbel", "帕赫贝尔"],
    "Christoph Willibald Gluck": ["Gluck", "格鲁克"],
    "Carl Maria von Weber": ["C. M. von Weber", "Weber", "韦伯"],
    "Johann Nepomuk Hummel": ["Hummel", "胡梅尔"],
    "Muzio Clementi": ["Clementi", "克莱门蒂"],
    "John Field": ["=Field", "=John Field"],
    "Richard Wagner": ["Wagner", "瓦格纳"],
    "Alexander Tcherepnin": ["Tcherepnin", "Александр Черепнин", "Александр Николаевич Черепнин"],
    "Nikolai Tcherepnin": ["Николай Черепнин"],
    "Anton Bruckner": ["Bruckner", "布鲁克纳"],
    "Gustav Mahler": ["Mahler", "马勒"],
    "Hugo Wolf": ["=Wolf", "=Hugo Wolf"],
    "Richard Strauss": ["R. Strauss", "理查·施特劳斯", "理查德·施特劳斯"],
    "Johann Strauss II": ["Johann Strauss", "Johann Strauss Jr.", "J. Strauss", "约翰·施特劳斯", "小约翰·施特劳斯"],
    "Josef Strauss": ["Jos. Strauss"],
    "Max Reger": ["Reger"],
    "Arnold Schoenberg": ["Schoenberg", "Schönberg", "勋伯格"],
    "Alban Berg": ["=Berg", "Alban Berg"],
    "Anton Webern": ["Webern"],
    "Paul Hindemith": ["Hindemith", "欣德米特"],
    "Kurt Weill": ["=Weill"],
    "Carl Orff": ["Orff", "奥尔夫"],
    "Sergei Rachmaninoff": ["Сергей Рахманинов", "Рахманинов", "Rachmaninov", "Rachmaninoff", "Rakhmaninov", "拉赫玛尼诺夫"],
    "Sergei Prokofiev": ["Сергей Прокофьев", "Прокофьев", "Prokofiev", "Prokofieff", "普罗科菲耶夫"],
    "Dmitri Shostakovich": ["Дмитрий Шостакович", "Шостакович", "Shostakovich", "Schostakowitsch", "肖斯塔科维奇"],
    "Igor Stravinsky": ["Игорь Стравинский", "Стравинский", "Stravinsky", "斯特拉文斯基"],
    "Modest Mussorgsky": ["Модест Мусоргский", "Мусоргский", "Mussorgsky", "Moussorgsky", "穆索尔斯基"],
    "Nikolai Rimsky-Korsakov": ["Николай Римский-Корсаков", "Римский-Корсаков", "Rimsky-Korsakov", "Rimsky Korsakov", "里姆斯基-科萨科夫"],
    "Alexander Borodin": ["Александр Бородин", "Бородин", "Borodin", "鲍罗丁"],
    "Mily Balakirev": ["Balakirev"],
    "Mikhail Glinka": ["Михаил Глинка", "Глинка", "Glinka", "格林卡"],
    "Alexander Scriabin": ["Александр Скрябин", "Скрябин", "Scriabin", "Skryabin", "斯克里亚宾"],
    "Alexander Glazunov": ["Александр Глазунов", "Глазунов", "Glazunov", "格拉祖诺夫"],
    "Anton Arensky": ["Arensky"],
    "Nikolai Medtner": ["Николай Метнер", "Метнер", "Medtner", "梅特纳"],
    "Aram Khachaturian": ["Арам Хачатурян", "Хачатурян", "Khachaturian", "哈恰图良"],
    "Béla Bartók": ["Bartók", "Bartok", "巴托克"],
    "Zoltán Kodály": ["Kodály", "Kodaly", "柯达伊"],
    "Ernő Dohnányi": ["Dohnányi", "Dohnanyi"],
    "Karol Szymanowski": ["Szymanowski"],
    "Henryk Wieniawski": ["Wieniawski", "维尼亚夫斯基"],
    "Ignacy Jan Paderewski": ["Paderewski"],
    "Edward Elgar": ["Elgar", "埃尔加"],
    "Ralph Vaughan Williams": ["Vaughan Williams", "沃恩·威廉斯"],
    "Gustav Holst": ["Holst", "霍尔斯特"],
    "Benjamin Britten": ["Britten", "布里顿"],
    "Frederick Delius": ["Delius"],
    "Hubert Parry": ["Charles Hubert Hastings Parry", "Parry"],
    "William Walton": ["=Walton", "William Walton"],
    "Thomas Tallis": ["Tallis"],
    "William Byrd": ["=Byrd", "William Byrd"],
    "John Dowland": ["Dowland"],
    "Isaac Albéniz": ["Albéniz", "Albeniz", "阿尔贝尼斯"],
    "Enrique Granados": ["Granados", "格拉纳多斯"],
    "Manuel de Falla": ["de Falla", "Falla", "法雅"],
    "Joaquín Rodrigo": ["Rodrigo", "罗德里戈"],
    "Pablo de Sarasate": ["Sarasate", "萨拉萨蒂"],
    "Heitor Villa-Lobos": ["Villa-Lobos", "Villa Lobos"],
    "Astor Piazzolla": ["Piazzolla", "皮亚佐拉"],
    "George Gershwin": ["Gershwin", "格什温"],
    "Aaron Copland": ["Copland"],
    "Samuel Barber": ["=Barber", "Samuel Barber"],
    "Charles Ives": ["=Ives", "Charles Ives"],
    "Leonard Bernstein": ["=Bernstein", "Leonard Bernstein"],
    "Philip Glass": ["=Glass", "Philip Glass"],
    "Arvo Pärt": ["Pärt", "Part, Arvo"],
    "Fritz Kreisler": ["Kreisler", "克莱斯勒"],
    "Ferruccio Busoni": ["Busoni"],
    "Engelbert Humperdinck": ["Humperdinck"],
    "Max Bruch": ["Bruch", "布鲁赫"],
    "Henri Vieuxtemps": ["Vieuxtemps", "维厄当"],
    "Ernest Bloch": ["=Bloch", "Ernest Bloch"],
    "Édouard Lalo": ["Lalo", "拉罗"],
    "Léo Delibes": ["Delibes", "德利布"],
    "Jacques Offenbach": ["Offenbach", "奥芬巴赫"],
    "Joseph Lanner": ["Lanner"],
    "Ottorino Respighi ": [],
}
COMPOSERS.pop("Ottorino Respighi ")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold()


_EXACT: dict[str, str] = {}
_PATTERNS: list[tuple[re.Pattern, str]] = []
for canon, aliases in COMPOSERS.items():
    _EXACT[_fold(canon)] = canon
    for a in aliases:
        strict = a.startswith("=")
        a = a.lstrip("=")
        _EXACT[_fold(a)] = canon
        if not strict:
            body = re.escape(_fold(a)).replace(r"\ ", r"\s*").replace(r"\.", r"\.?")
            if re.search(r"[一-鿿]", a):
                pat = re.compile(body)
            else:
                pat = re.compile(rf"(?<![\w'-]){body}(?![\w'-])")
            _PATTERNS.append((pat, canon))
_PATTERNS.sort(key=lambda p: -len(p[0].pattern))
# "Surname, Given" 倒序写法
for canon in COMPOSERS:
    parts = canon.split()
    if len(parts) >= 2:
        _EXACT[_fold(f"{parts[-1]}, {' '.join(parts[:-1])}")] = canon


_JUNK = re.compile(r"^(?:n/?a|not applicable|unknown|未知|various|various artists|群星|\?+|-+)$", re.I)


def canonical(name: str | None) -> str | None:
    """把标签里的作曲家写法统一为规范名；不认识的原样返回（'Surname, Given' 改为 'Given Surname'）。"""
    if not name:
        return name
    name = re.sub(r"\s+", " ", name).strip()
    if _JUNK.match(name) or _JUNK.match(re.sub(r"^na\s+", "", name, flags=re.I)):
        return None
    key = _fold(name)
    if key in _EXACT:
        return _EXACT[key]
    found = find_all(name)
    if len(found) == 1 or (found and re.search(r"arr\.|transcri|bearb|改编", name, re.I)):
        return found[0]
    m = re.match(r"^([^,;/&\d]+),\s*([^,;/&\d]+)$", name)
    if m:
        return f"{m.group(2).strip()} {m.group(1).strip()}"
    return name


def find_all(text: str) -> list[str]:
    folded = _fold(text)
    hits: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for pat, canon in _PATTERNS:
        for m in pat.finditer(folded):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), canon))
    out = []
    for _, c in sorted(hits):
        if c not in out:
            out.append(c)
    return out


def find_one(text: str | None) -> str | None:
    if not text:
        return None
    found = find_all(text)
    return found[0] if len(found) == 1 else None


_PREFIX_SEP = re.compile(r"^\s*(?:[:：]|-|–|—|,)?\s*")


def split_prefix(title: str) -> tuple[str | None, str]:
    """'SCHUMANN Piano Concerto…' / 'Greg: Morning Mood' -> (作曲家, 去掉前缀的标题)。"""
    title = re.sub(r"^(?:Sir|Dame)\s+", "", title.strip())
    folded = _fold(title)
    for pat, canon in _PATTERNS:
        m = pat.match(folded)
        if m and m.start() == 0:
            rest = title[m.end():]
            sep = _PREFIX_SEP.match(rest)
            rest2 = rest[sep.end():] if sep else rest
            if rest2 and (rest[:1] in ":：-–—," or rest[:1] == " "):
                return canon, rest2.strip()
    return None, title
