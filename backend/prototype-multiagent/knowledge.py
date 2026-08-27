"""Синтетический срез каталога: чанки для векторного поиска и граф знаний.

Реальные данные Заказчика в репозиторий не попадают (раздел А.9 плана
обеспечения данными), поэтому здесь придуманный, но структурно достоверный
набор: перепаки по одной пресс-форме, алиасы одной сущности, закрытые
закупочные цены рядом с публичными наименованиями в одном документе.

Гриф присваивается чанку, а не документу. Прайс поставщика намеренно разрезан
так, что наименование позиции публично, а закупочная цена закрыта: это главное
ограничение чанкинга из документа "Конвейер извлечения", раздел 1.
"""

from dataclasses import dataclass, field

PUBLIC, INTERNAL, CONFIDENTIAL = "public", "internal", "confidential"


@dataclass
class Chunk:
    id: str
    text: str
    acl: str
    doc_id: str
    product_id: str | None = None
    source_type: str = "card"


@dataclass
class Node:
    id: str
    kind: str          # Product | Prototype | Party | Document
    title: str
    acl: str = PUBLIC
    attrs: dict = field(default_factory=dict)


# --- граф: узлы -----------------------------------------------------------

NODES: dict[str, Node] = {n.id: n for n in [
    Node("P-VAZ2101", "Prototype", "ВАЗ-2101",
         attrs={"aliases": ["Жигули", "Lada 1200", "копейка", "ВАЗ 2101"],
                "years": "1970-1988", "marque": "PARTY-AVTOVAZ"}),
    Node("P-W124", "Prototype", "Mercedes-Benz W124",
         attrs={"aliases": ["W124", "Мерседес 124 кузов", "E-Class первое поколение"],
                "years": "1984-1997", "marque": "PARTY-MB"}),

    Node("PARTY-AVTOVAZ", "Party", "АвтоВАЗ", attrs={"role": "marque"}),
    Node("PARTY-MB", "Party", "Mercedes-Benz", attrs={"role": "marque"}),
    Node("PARTY-IXO", "Party", "IXO", attrs={"role": "manufacturer"}),
    Node("PARTY-NAP", "Party", "Наш Автопром", attrs={"role": "manufacturer"}),
    Node("PARTY-ALT", "Party", "Altaya", attrs={"role": "manufacturer"}),
    Node("PARTY-NOR", "Party", "Norev", attrs={"role": "manufacturer"}),
    Node("PARTY-MIN", "Party", "Minichamps", attrs={"role": "manufacturer"}),
    Node("PARTY-SUP-A", "Party", "Поставщик А", acl=INTERNAL, attrs={"role": "supplier"}),

    Node("IXO-2101-BL", "Product", "ВАЗ-2101 синий, IXO, 1:18",
         attrs={"scale": "1:18", "colour": "синий", "tooling": "T-VAZ2101",
                "year": 2019, "edition": 1500, "height_mm": 118}),
    Node("NAP-2101-RD", "Product", "ВАЗ-2101 красный, Наш Автопром, 1:18",
         attrs={"scale": "1:18", "colour": "красный", "tooling": "T-VAZ2101",
                "year": 2023, "edition": 900, "height_mm": 118}),
    Node("ALT-2101-WH", "Product", "ВАЗ-2101 белый, Altaya, 1:18",
         attrs={"scale": "1:18", "colour": "белый", "tooling": "T-VAZ2101",
                "year": 2021, "edition": 2000, "height_mm": 118}),
    Node("MIN-W124-SL", "Product", "Mercedes-Benz W124 серебристый, Minichamps, 1:18",
         attrs={"scale": "1:18", "colour": "серебристый", "tooling": "T-W124",
                "year": 2022, "edition": 700, "height_mm": 118}),
    Node("NOR187001", "Product", "Витрина Norev для моделей 1:18",
         attrs={"scale": "1:18", "kind": "accessory", "inner_height_mm": 120}),

    Node("doc-catalog-ixo-2026", "Document", "Каталог IXO 2026", attrs={"pages": 120}),
    Node("doc-card-2101", "Document", "Карточка сайта ВАЗ-2101"),
    Node("doc-price-a-2026-08", "Document", "Прайс Поставщика А, август 2026",
         acl=INTERNAL),
]}

# --- граф: связи ----------------------------------------------------------
# Направление MENTIONS - от документа к упомянутой сущности (БФТ, FR-1.3).

EDGES: list[tuple[str, str, str]] = [
    ("IXO-2101-BL", "DEPICTS", "P-VAZ2101"),
    ("NAP-2101-RD", "DEPICTS", "P-VAZ2101"),
    ("ALT-2101-WH", "DEPICTS", "P-VAZ2101"),
    ("MIN-W124-SL", "DEPICTS", "P-W124"),

    ("IXO-2101-BL", "MADE_BY", "PARTY-IXO"),
    ("NAP-2101-RD", "MADE_BY", "PARTY-NAP"),
    ("ALT-2101-WH", "MADE_BY", "PARTY-ALT"),
    ("MIN-W124-SL", "MADE_BY", "PARTY-MIN"),
    ("NOR187001", "MADE_BY", "PARTY-NOR"),

    ("P-VAZ2101", "OF_MARQUE", "PARTY-AVTOVAZ"),
    ("P-W124", "OF_MARQUE", "PARTY-MB"),

    # Перепаки: одна пресс-форма, разные бренды. Ради этого класса вопросов
    # и строится граф - векторным поиском такая связь не находится.
    ("IXO-2101-BL", "SAME_TOOLING_AS", "NAP-2101-RD"),
    ("NAP-2101-RD", "SAME_TOOLING_AS", "ALT-2101-WH"),
    ("IXO-2101-BL", "SAME_TOOLING_AS", "ALT-2101-WH"),

    ("PARTY-SUP-A", "SUPPLIES", "IXO-2101-BL"),
    ("doc-catalog-ixo-2026", "MENTIONS", "IXO-2101-BL"),
    ("doc-card-2101", "MENTIONS", "IXO-2101-BL"),
    ("doc-price-a-2026-08", "MENTIONS", "IXO-2101-BL"),
]

# --- чанки ----------------------------------------------------------------

CHUNKS: list[Chunk] = [
    Chunk("c01", "ВАЗ-2101 синий, производитель IXO, масштаб 1:18, выпуск 2019 года, "
                 "тираж 1500 экземпляров. Артикул IXO-2101-BL. Высота модели 118 мм.",
          PUBLIC, "doc-card-2101", "IXO-2101-BL", "card"),
    Chunk("c02", "ВАЗ-2101 красный, производитель Наш Автопром, масштаб 1:18, "
                 "выпуск 2023 года, тираж 900. Артикул NAP-2101-RD.",
          PUBLIC, "doc-card-2101", "NAP-2101-RD", "card"),
    Chunk("c03", "ВАЗ-2101 белый, производитель Altaya, масштаб 1:18, выпуск 2021 года, "
                 "тираж 2000. Артикул ALT-2101-WH.",
          PUBLIC, "doc-card-2101", "ALT-2101-WH", "card"),
    Chunk("c04", "Mercedes-Benz W124 серебристый, Minichamps, масштаб 1:18, 2022 год, "
                 "тираж 700. Артикул MIN-W124-SL. Высота модели 118 мм.",
          PUBLIC, "doc-card-2101", "MIN-W124-SL", "card"),
    Chunk("c05", "Витрина Norev для моделей масштаба 1:18. Артикул NOR187001. "
                 "Внутренняя высота 120 мм, подходит для стандартных моделей 1:18.",
          PUBLIC, "doc-catalog-ixo-2026", "NOR187001", "catalog"),
    Chunk("c06", "Прототип ВАЗ-2101, также известен как Жигули, Lada 1200 и копейка. "
                 "Годы выпуска прототипа 1970-1988, марка АвтоВАЗ.",
          PUBLIC, "doc-catalog-ixo-2026", None, "catalog"),
    Chunk("c07", "Каталог IXO 2026, раздел советских легковых автомобилей: "
                 "модели ВАЗ выпускаются по пресс-форме T-VAZ2101.",
          PUBLIC, "doc-catalog-ixo-2026", "IXO-2101-BL", "catalog"),

    # Одна строка прайса, разрезанная по границе грифа. Наименование публично,
    # закупочная цена - нет. Резать иначе нельзя: утечёт или пропадёт.
    Chunk("c08", "Позиция прайса Поставщика А за август 2026: ВАЗ-2101 синий IXO 1:18, "
                 "артикул IXO-2101-BL, срок поставки 14 дней.",
          PUBLIC, "doc-price-a-2026-08", "IXO-2101-BL", "price"),
    Chunk("c09", "Закупочная цена позиции IXO-2101-BL по прайсу Поставщика А: "
                 "2 450 руб. Маржинальность позиции 38 процентов.",
          CONFIDENTIAL, "doc-price-a-2026-08", "IXO-2101-BL", "price"),
    Chunk("c10", "Условия договора с Поставщиком А: отсрочка платежа 45 дней, "
                 "ретробонус 4 процента при выборке от 300 позиций в квартал.",
          CONFIDENTIAL, "doc-price-a-2026-08", None, "price"),
    Chunk("c11", "Остаток на складе по артикулу IXO-2101-BL: 12 штук, "
                 "резерв 2 штуки. Данные учётной системы.",
          INTERNAL, "doc-card-2101", "IXO-2101-BL", "card"),
]

CHUNK_BY_ID = {c.id: c for c in CHUNKS}


def neighbours(node_id: str, acl: list[str]) -> list[tuple[str, str]]:
    """Соседи узла с предикатом ACL прямо в выборке, а не после неё.

    Именно так работает и Cypher в целевой системе: гриф - атрибут узла,
    и недоступные узлы не извлекаются, а не отфильтровываются потом.
    """
    out = []
    for src, rel, dst in EDGES:
        if src == node_id and NODES[dst].acl in acl:
            out.append((rel, dst))
        elif dst == node_id and NODES[src].acl in acl:
            out.append((rel + "_OF", src))
    return out


def traverse(seeds: list[str], acl: list[str], depth: int) -> list[str]:
    """Обход графа на заданную глубину. Глубину выбирает агент, не константа."""
    seen, frontier = set(seeds), list(seeds)
    for _ in range(depth):
        nxt = []
        for node_id in frontier:
            if node_id not in NODES:
                continue
            for _rel, dst in neighbours(node_id, acl):
                if dst not in seen:
                    seen.add(dst)
                    nxt.append(dst)
        frontier = nxt
        if not frontier:
            break
    return [n for n in seen if n in NODES]


def describe(node_id: str) -> str:
    n = NODES[node_id]
    bits = [f"{n.kind} {n.id}: {n.title}"]
    for k, v in n.attrs.items():
        bits.append(f"{k}={v}")
    return ", ".join(bits)
