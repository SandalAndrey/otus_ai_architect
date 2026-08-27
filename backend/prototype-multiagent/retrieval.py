"""Векторный поиск и переранжирование с фильтром по грифу в самом запросе.

Ключевое свойство, ради которого написан этот модуль: фильтр по ACL входит в
выборку, а не применяется к её результату (ADR-0002). Разница видна на узком
доступе - при постфильтрации выдача схлопывается, потому что отобранные top-k
отсеиваются целиком.

Эмбеддинги и реранкер настоящие (bge-m3, bge-reranker-v2-m3). Если они не
установлены, модуль откатывается на детерминированный хеш-эмбеддинг: это
позволяет прогнать логику агентов, но качество поиска при этом не показатель.
"""

from __future__ import annotations

import hashlib
import math
import os
import time
from dataclasses import dataclass

from knowledge import CHUNKS, Chunk


@dataclass
class Hit:
    chunk: Chunk
    score: float


@dataclass
class Timing:
    embed: float = 0.0
    search: float = 0.0
    rerank: float = 0.0


# --- эмбеддинги -----------------------------------------------------------

class HashEmbedder:
    """Запасной вариант без внешних зависимостей. Не для оценки качества."""

    name = "hash-fallback"
    dim = 256

    def encode(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in _tokens(text):
                h = int(hashlib.md5(token.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            out.append(_norm(vec))
        return out


class BGEEmbedder:
    """bge-m3: плотный вектор. Разреженный здесь не используется намеренно -
    в прототипе разреженную часть заменяет собственный BM25 по токенам, чтобы
    не тянуть в зависимости весь стек FlagEmbedding ради одного поля."""

    name = "BAAI/bge-m3"

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer("BAAI/bge-m3")
        self.dim = self.model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str]) -> list[list[float]]:
        vecs = self.model.encode(texts, normalize_embeddings=True,
                                 show_progress_bar=False)
        return [list(map(float, v)) for v in vecs]


def build_embedder():
    if os.getenv("EMBEDDER", "bge").strip().lower() == "hash":
        return HashEmbedder()
    try:
        return BGEEmbedder()
    except Exception as exc:                      # noqa: BLE001
        print(f"[retrieval] bge-m3 недоступна ({exc.__class__.__name__}), "
              f"откат на хеш-эмбеддинг. Качество поиска не показательно.")
        return HashEmbedder()


# --- реранкер -------------------------------------------------------------

class NoReranker:
    name = "нет"

    def rank(self, query: str, hits: list[Hit]) -> list[Hit]:
        return hits


class BGEReranker:
    name = "BAAI/bge-reranker-v2-m3"

    def __init__(self) -> None:
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder("BAAI/bge-reranker-v2-m3")

    def rank(self, query: str, hits: list[Hit]) -> list[Hit]:
        if not hits:
            return hits
        scores = self.model.predict([(query, h.chunk.text) for h in hits])
        for hit, score in zip(hits, scores):
            hit.score = float(score)
        return sorted(hits, key=lambda h: -h.score)


def build_reranker():
    if os.getenv("RERANKER", "bge").strip().lower() in ("off", "none"):
        return NoReranker()
    try:
        return BGEReranker()
    except Exception as exc:                      # noqa: BLE001
        print(f"[retrieval] реранкер недоступен ({exc.__class__.__name__}), "
              f"шаг пропускается.")
        return NoReranker()


# --- индекс ---------------------------------------------------------------

class Index:
    """Векторный и разреженный поиск с обязательным предикатом ACL.

    Метод search принимает acl и применяет его до ранжирования. Обойти этот
    параметр нельзя - у метода нет режима "без фильтра".
    """

    def __init__(self, embedder, reranker) -> None:
        self.embedder = embedder
        self.reranker = reranker
        self.chunks = CHUNKS
        self.vectors = embedder.encode([c.text for c in self.chunks])
        self.df = _document_frequencies(self.chunks)
        self.timing = Timing()

    def candidates(self, query: str, acl: list[str],
                   top_dense: int = 30, top_sparse: int = 20) -> list[Hit]:
        """Кандидаты без переранжирования: его выполняет отдельный шаг графа,
        как и на диаграмме уровня 3 - переранжировщик стоит после обоих
        агентов извлечения, а не внутри одного из них."""
        t0 = time.perf_counter()
        qvec = self.embedder.encode([query])[0]
        self.timing.embed += time.perf_counter() - t0

        t0 = time.perf_counter()
        # Предикат по грифу применяется здесь, вместе с отбором кандидатов.
        allowed = [(i, c) for i, c in enumerate(self.chunks) if c.acl in acl]

        dense = sorted(((_cos(qvec, self.vectors[i]), c) for i, c in allowed),
                       key=lambda x: -x[0])[:top_dense]
        sparse = sorted(((_bm25(query, c.text, self.df, len(self.chunks)), c)
                         for _i, c in allowed), key=lambda x: -x[0])[:top_sparse]
        fused = _rrf([[c for _s, c in dense], [c for _s, c in sparse]])
        self.timing.search += time.perf_counter() - t0
        return fused

    def rerank(self, query: str, hits: list[Hit], top: int = 8) -> list[Hit]:
        t0 = time.perf_counter()
        ranked = self.reranker.rank(query, hits)
        self.timing.rerank += time.perf_counter() - t0
        return ranked[:top]


# --- вспомогательное ------------------------------------------------------

def _tokens(text: str) -> list[str]:
    out, buf = [], []
    for ch in text.lower():
        if ch.isalnum() or ch == "-":
            buf.append(ch)
        elif buf:
            out.append("".join(buf))
            buf = []
    if buf:
        out.append("".join(buf))
    return out


def _norm(vec: list[float]) -> list[float]:
    length = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / length for v in vec]


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _document_frequencies(chunks: list[Chunk]) -> dict[str, int]:
    df: dict[str, int] = {}
    for chunk in chunks:
        for token in set(_tokens(chunk.text)):
            df[token] = df.get(token, 0) + 1
    return df


def _bm25(query: str, text: str, df: dict[str, int], n_docs: int,
          k1: float = 1.5, b: float = 0.75, avg_len: float = 30.0) -> float:
    """Разреженная часть гибридного поиска.

    Нужна из-за артикулов: строка NOR187001 не имеет семантики, и плотный
    поиск на ней работает плохо.
    """
    doc = _tokens(text)
    score = 0.0
    for token in _tokens(query):
        freq = doc.count(token)
        if not freq:
            continue
        idf = math.log(1 + (n_docs - df.get(token, 0) + 0.5) / (df.get(token, 0) + 0.5))
        score += idf * freq * (k1 + 1) / (freq + k1 * (1 - b + b * len(doc) / avg_len))
    return score


def _rrf(lists: list[list[Chunk]], k: int = 60) -> list[Hit]:
    """Reciprocal Rank Fusion: не требует калибровки шкал, в отличие от
    взвешенной суммы разнородных оценок."""
    scores: dict[str, float] = {}
    seen: dict[str, Chunk] = {}
    for ranked in lists:
        for rank, chunk in enumerate(ranked):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank + 1)
            seen[chunk.id] = chunk
    order = sorted(scores.items(), key=lambda x: -x[1])
    return [Hit(seen[cid], score) for cid, score in order]
