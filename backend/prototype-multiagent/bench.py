"""Замер шагов извлечения на профиле A (CPU).

Отвечает на один вопрос: укладывается ли конвейер в NFR-1.2 (полный ответ не
более 5 с на p95) и что именно этот бюджет расходует.

Первый прогон демонстрации показал, что переранжирование занимает порядка 90
процентов времени при 8-11 кандидатах. В документе "Конвейер извлечения"
заложен вход top-50, поэтому нужна не одна точка, а зависимость от числа
кандидатов.

Запуск:
    python bench.py
    python bench.py --sizes 8,20,50,100 --repeats 5
"""

from __future__ import annotations

import argparse
import statistics
import time

from knowledge import CHUNKS, Chunk
from retrieval import Hit, build_embedder, build_reranker

QUERIES = [
    "Какой масштаб у артикула IXO-2101-BL",
    "Какие ещё бренды выпускали эту модель по той же пресс-форме",
    "Подойдёт ли витрина Norev к модели 1:18",
    "Чем отличается выпуск 2019 года от выпуска 2023",
]

NFR_1_2 = 5.0    # с, полный ответ на p95
NFR_1_1 = 1.5    # с, время до первого символа


def synthetic_candidates(n: int) -> list[Hit]:
    """Набор из n кандидатов. Чанков в прототипе мало, поэтому они
    размножаются с изменением текста: важна длина пары, а не её смысл."""
    out = []
    for i in range(n):
        base: Chunk = CHUNKS[i % len(CHUNKS)]
        out.append(Hit(Chunk(f"{base.id}-{i}", f"{base.text} (вариант {i})",
                             base.acl, base.doc_id, base.product_id,
                             base.source_type), 0.0))
    return out


def measure(fn, repeats: int) -> tuple[float, float]:
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times), max(times)


def main() -> None:
    parser = argparse.ArgumentParser(description="Замер конвейера извлечения")
    parser.add_argument("--sizes", default="8,20,50",
                        help="Размеры входа переранжировщика через запятую")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]

    embedder = build_embedder()
    reranker = build_reranker()
    print(f"Эмбеддинги: {embedder.name}\nРеранкер:   {reranker.name}\n")

    # Прогрев: первый вызов включает подготовку графа вычислений и в замер
    # попадать не должен.
    embedder.encode([QUERIES[0]])
    reranker.rank(QUERIES[0], synthetic_candidates(4))

    print("Эмбеддинги одного запроса")
    med, worst = measure(lambda: embedder.encode([QUERIES[0]]), args.repeats)
    print(f"  медиана {med * 1000:6.0f} мс   худшее {worst * 1000:6.0f} мс\n")

    print("Переранжирование, зависимость от числа кандидатов")
    print(f"  {'кандидатов':>10} {'медиана, с':>12} {'худшее, с':>11} "
          f"{'на пару, мс':>12} {'доля NFR-1.2':>13}")
    per_pair = []
    for n in sizes:
        cands = synthetic_candidates(n)
        med, worst = measure(lambda c=cands: reranker.rank(QUERIES[1], list(c)),
                             args.repeats)
        pair_ms = med / n * 1000
        per_pair.append(pair_ms)
        print(f"  {n:>10} {med:>12.2f} {worst:>11.2f} {pair_ms:>12.1f} "
              f"{med / NFR_1_2 * 100:>12.0f} %")

    if per_pair:
        avg_pair = statistics.median(per_pair) / 1000
        print("\nВыводы")
        print(f"  Стоимость одной пары вопрос-кандидат: {avg_pair * 1000:.1f} мс")
        for n in (20, 50):
            proj = avg_pair * n
            verdict = "укладывается" if proj < NFR_1_2 else "НЕ укладывается"
            print(f"  Прогноз для top-{n}: {proj:.2f} с - {verdict} в NFR-1.2 "
                  f"({NFR_1_2} с) даже без единого вызова модели")
        budget = NFR_1_2 - NFR_1_1
        print(f"  Кандидатов, влезающих в остаток бюджета после TTFT "
              f"({budget:.1f} с): {int(budget / avg_pair)}")


if __name__ == "__main__":
    main()
