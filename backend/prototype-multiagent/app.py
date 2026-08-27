"""Мультиагентный онлайн-путь на LangGraph.

Реализует схему из ADR-0007: супервизор и четыре специализированных агента,
плюс детерминированные компоненты (guardrails, переранжировщик, аудит).
Агенты отличаются от компонентов тем, что их поведение определяется выводом
модели; guardrails и переранжировщик агентами намеренно не сделаны.

Запуск:
    python app.py "Какой масштаб у артикула IXO-2101-BL" --acl public
    python app.py --demo
"""

from __future__ import annotations

import argparse
import operator
import os
import re
import time
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

import knowledge as kb
from llm import build_llm
from retrieval import Hit, Index, build_embedder, build_reranker

MAX_STEPS_LIMIT = 6      # потолок из спецификации API, backend/openapi.yaml


class State(TypedDict, total=False):
    question: str
    acl: list[str]
    max_steps: int
    klass: str
    need_graph: bool
    hits: list[Hit]
    graph_facts: list[str]
    facts: list[str]
    answer: str | None
    refusal: dict | None
    citations: list[dict]
    # steps суммируется: при параллельном запуске агентов в это поле
    # пишут сразу два узла, и обычное присваивание ломает граф.
    steps: Annotated[int, operator.add]
    trace: Annotated[list[str], operator.add]


# --- детерминированные компоненты -----------------------------------------

INJECTION = re.compile(
    r"(игнорируй|забудь).{0,30}(инструкц|предыдущ)|ignore (all )?previous|"
    r"system prompt|ты теперь", re.I)


def guard_in(state: State) -> dict:
    """Входные guardrails. Правило, а не модель: результат обязан быть
    воспроизводимым, иначе проверку нельзя предъявить на приёмке."""
    if INJECTION.search(state["question"]):
        return {"refusal": {"code": "guardrails_rejected",
                            "message": "Запрос содержит попытку переопределить инструкции"},
                "trace": ["guard_in: отклонено, попытка инъекции"]}
    return {"trace": ["guard_in: чисто"]}


def guard_out(state: State) -> dict:
    """Выходные guardrails и аудит. Число отсечённых по грифу узлов пишется
    в журнал и границу сервиса не пересекает (см. backend/openapi.yaml)."""
    denied = sum(1 for c in kb.CHUNKS if c.acl not in state["acl"])
    return {"trace": [f"guard_out: аудит записан, отсечено по грифу узлов: {denied}"]}


def rerank_node(state: State, index: Index) -> dict:
    """Переранжировщик. Cross-encoder без участия LLM."""
    hits = index.rerank(state["question"], state.get("hits", []))
    facts = [f"[{h.chunk.doc_id}#{h.chunk.id}] {h.chunk.text}" for h in hits]
    return {"hits": hits,
            "facts": facts + state.get("graph_facts", []),
            "trace": [f"rerank: отобрано {len(hits)} чанков"]}


# --- агенты ---------------------------------------------------------------

def supervisor(state: State, llm) -> dict:
    """Супервизор-маршрутизатор. Классификация и план одним вызовом модели:
    отдельного шага на классификацию не заводится, иначе адаптивная
    маршрутизация начнёт сама себе стоить дороже, чем экономит."""
    plan = llm.json(
        "supervisor",
        "Ты планировщик поиска по каталогу масштабных моделей. Определи класс "
        "вопроса. Класс A - факт об одной позиции, достаточно поиска по тексту. "
        "Класс B - нужно сопоставить несколько позиций или пройти по связям "
        "(перепаки, отличия выпусков, аналоги). Верни "
        '{"class":"A"|"B","need_graph":true|false,"reason":"..."}',
        state["question"],
        default={"class": "B", "need_graph": True, "reason": "разбор не удался"})
    klass = "B" if str(plan.get("class", "B")).upper() == "B" else "A"
    need_graph = bool(plan.get("need_graph", klass == "B"))
    return {"klass": klass, "need_graph": need_graph,
            "steps": 1,
            "trace": [f"supervisor: класс {klass}, обход графа {'да' if need_graph else 'нет'}"
                      f" ({plan.get('reason', '')})"]}


def vector_agent(state: State, llm, index: Index) -> dict:
    """Агент векторного поиска. Раскрывает алиасы и написания одной сущности -
    это решение, а не параметр запроса, поэтому шаг агентский."""
    plan = llm.json(
        "vector",
        "Перепиши вопрос в 1-3 поисковых запроса по каталогу. Раскрой алиасы "
        "и разные написания одной сущности (например ВАЗ-2101, Жигули, Lada 1200). "
        'Верни {"queries":["...","..."]}',
        state["question"], default={"queries": [state["question"]]})
    queries = [q for q in plan.get("queries", []) if isinstance(q, str)][:3] \
        or [state["question"]]

    merged: dict[str, Hit] = {}
    for query in queries:
        for hit in index.candidates(query, state["acl"]):
            prev = merged.get(hit.chunk.id)
            if prev is None or hit.score > prev.score:
                merged[hit.chunk.id] = hit
    hits = list(merged.values())
    return {"hits": hits, "steps": 1,
            "trace": [f"vector_agent: запросы {queries}, кандидатов {len(hits)}"]}


def graph_agent(state: State, llm, index: Index) -> dict:
    """Агент обхода графа. Глубину выбирает по вопросу, а не берёт константой:
    один хоп для производителя, три для перепаков."""
    plan = llm.json(
        "graph",
        "Выбери глубину обхода графа знаний для вопроса. 1 - атрибут или "
        "производитель, 2 - отличия выпусков, 3 - перепаки по общей пресс-форме. "
        'Верни {"depth":1|2|3}',
        state["question"], default={"depth": 2})
    try:
        depth = max(1, min(3, int(plan.get("depth", 2))))
    except (TypeError, ValueError):
        depth = 2

    # Точки входа - артикулы из чанков, найденных векторным агентом.
    seeds = [h.chunk.product_id for h in state.get("hits", []) if h.chunk.product_id]
    if not seeds:
        seeds = [n.id for n in kb.NODES.values()
                 if n.kind == "Product" and n.acl in state["acl"]][:3]
    seeds = list(dict.fromkeys(seeds))[:5]

    node_ids = kb.traverse(seeds, state["acl"], depth)
    facts = [f"[граф] {kb.describe(nid)}" for nid in node_ids]
    return {"graph_facts": facts, "steps": 1,
            "trace": [f"graph_agent: глубина {depth}, точки входа {seeds}, "
                      f"узлов {len(node_ids)}"]}


def composer(state: State, llm) -> dict:
    """Агент-составитель. Собирает ответ только из извлечённых фактов."""
    facts = state.get("facts", [])
    if not facts:
        return {"refusal": {"code": "no_supporting_data",
                            "message": "Нет данных, подтверждающих ответ. "
                                       "Уточните у закупщика."},
                "trace": ["composer: подтверждающих фактов нет, сформирован отказ"]}
    answer = llm.complete(
        "composer",
        "Ответь на вопрос строго по приведённым фактам. Не добавляй ничего, "
        "чего в фактах нет. Если фактов не хватает, так и напиши. Кратко.",
        f"ВОПРОС: {state['question']}\n\nФАКТЫ:\n" + "\n".join(facts))
    citations = [{"type": "document", "id": h.chunk.doc_id, "chunk": h.chunk.id}
                 for h in state.get("hits", [])[:3]]
    return {"answer": answer, "citations": citations,
            "steps": 1,
            "trace": [f"composer: ответ собран, источников {len(citations)}"]}


# Составитель может честно ответить "фактов не хватает". Это не утверждение
# о мире, а признание нехватки данных, и проверять его нечего.
NO_DATA = re.compile(r"не\s*хватает|недостаточно|нет\s+(данных|информации)|"
                     r"не\s+(указан|найден|содержится)", re.I)


def verifier(state: State, llm) -> dict:
    """Агент-верификатор. Сверяет утверждения уже готового ответа: до сборки
    сверять нечего.

    Первый прогон прототипа дал два ложных отказа из четырёх вопросов.
    Механика: составитель писал "информации недостаточно", верификатор честно
    не находил подтверждения этому тезису и возвращал supported=false, что
    превращалось в отказ при наличии данных. Логика инвертировалась.

    Отсюда два правила. Ответ-признание нехватки данных проверке не подлежит.
    Проверяются только положительные фактические утверждения, и отказ ставится
    лишь тогда, когда хотя бы одно из них названо неподтверждённым явно.
    """
    if state.get("refusal"):
        return {"trace": ["verifier: пропущен, ответа нет"]}

    answer = state.get("answer") or ""
    if NO_DATA.search(answer):
        return {"steps": 1,
                "trace": ["verifier: ответ признаёт нехватку данных, проверка не нужна"]}

    verdict = llm.json(
        "verifier",
        "Ты проверяешь достоверность ответа по фактам. Выпиши положительные "
        "фактические утверждения ответа и укажи, какие из них НЕ подтверждаются "
        "фактами. Признание нехватки данных утверждением не считается. Верни "
        '{"unsupported":["..."],"note":"..."}. Пустой список означает, что всё '
        "подтверждено.",
        f"ОТВЕТ: {answer}\n\nФАКТЫ:\n" + "\n".join(state.get("facts", [])),
        default={"unsupported": [], "note": "разбор не удался"})

    unsupported = [u for u in verdict.get("unsupported", []) if isinstance(u, str) and u.strip()]
    if unsupported:
        return {"answer": None,
                "refusal": {"code": "no_supporting_data",
                            "message": "Не удалось подтвердить ответ источниками."},
                "steps": 1,
                "trace": [f"verifier: не подтверждено {len(unsupported)} утверждений, отказ"]}
    return {"steps": 1, "trace": ["verifier: утверждения подтверждены"]}


# --- сборка графа ---------------------------------------------------------

def route_after_guard(state: State) -> str:
    return END if state.get("refusal") else "supervisor"


def route_after_supervisor(state: State) -> list[str]:
    """Ветвление и параллелизм. Класс A идёт коротким путём, класс B
    запускает оба извлечения одновременно - в этом смысл Plan-and-Execute
    против последовательного ReAct."""
    if state.get("steps", 0) >= min(state.get("max_steps", 3), MAX_STEPS_LIMIT):
        return ["rerank"]
    if state.get("need_graph"):
        return ["vector_agent", "graph_agent"]
    return ["vector_agent"]


def build_app(llm, index: Index):
    graph = StateGraph(State)
    graph.add_node("guard_in", guard_in)
    graph.add_node("supervisor", lambda s: supervisor(s, llm))
    graph.add_node("vector_agent", lambda s: vector_agent(s, llm, index))
    graph.add_node("graph_agent", lambda s: graph_agent(s, llm, index))
    graph.add_node("rerank", lambda s: rerank_node(s, index))
    graph.add_node("composer", lambda s: composer(s, llm))
    graph.add_node("verifier", lambda s: verifier(s, llm))
    graph.add_node("guard_out", guard_out)

    graph.add_edge(START, "guard_in")
    graph.add_conditional_edges("guard_in", route_after_guard,
                                {"supervisor": "supervisor", END: END})
    graph.add_conditional_edges("supervisor", route_after_supervisor,
                                ["vector_agent", "graph_agent", "rerank"])
    graph.add_edge("vector_agent", "rerank")
    graph.add_edge("graph_agent", "rerank")
    graph.add_edge("rerank", "composer")
    graph.add_edge("composer", "verifier")
    graph.add_edge("verifier", "guard_out")
    graph.add_edge("guard_out", END)
    return graph.compile()


def ask(app, question: str, acl: list[str], max_steps: int = 3) -> dict:
    return app.invoke({"question": question, "acl": acl, "max_steps": max_steps,
                       "hits": [], "graph_facts": [], "trace": []})


# --- запуск ---------------------------------------------------------------

DEMO = [
    ("Какой масштаб у артикула IXO-2101-BL", ["public"]),
    ("Какие ещё бренды выпускали эту модель по той же пресс-форме", ["public"]),
    ("Какая закупочная цена и маржа по IXO-2101-BL", ["public", "internal"]),
    ("Какая закупочная цена и маржа по IXO-2101-BL", ["public", "internal", "confidential"]),
]


def show(result: dict, elapsed: float, llm) -> None:
    for line in result.get("trace", []):
        print("   ", line)
    if result.get("refusal"):
        print(f"    ОТКАЗ: {result['refusal']['code']} - {result['refusal']['message']}")
    else:
        print(f"    ОТВЕТ: {result.get('answer')}")
        print(f"    Источники: {result.get('citations')}")
    print(f"    Шагов: {result.get('steps')}, вызовов модели: {llm.usage.calls}, "
          f"время {elapsed:.2f} с")


def main() -> None:
    parser = argparse.ArgumentParser(description="Мультиагентный прототип")
    parser.add_argument("question", nargs="?", help="Вопрос")
    parser.add_argument("--acl", default="public",
                        help="Разрешённые грифы через запятую")
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--demo", action="store_true", help="Прогнать набор сценариев")
    args = parser.parse_args()

    llm = build_llm()
    index = Index(build_embedder(), build_reranker())
    app = build_app(llm, index)
    print(f"Модель: {os.getenv('LLM_PROVIDER', 'stub')}, "
          f"эмбеддинги: {index.embedder.name}, реранкер: {index.reranker.name}\n")

    cases = DEMO if args.demo or not args.question else \
        [(args.question, [a.strip() for a in args.acl.split(",") if a.strip()])]

    for question, acl in cases:
        print(f"[{','.join(acl)}] {question}")
        before = llm.usage.calls
        t0 = time.perf_counter()
        result = ask(app, question, acl, args.max_steps)
        elapsed = time.perf_counter() - t0
        show(result, elapsed, llm)
        print(f"    (вызовов на этот вопрос: {llm.usage.calls - before})\n")

    t = index.timing
    print(f"Суммарно по шагам извлечения: эмбеддинги {t.embed:.2f} с, "
          f"поиск {t.search:.2f} с, переранжирование {t.rerank:.2f} с")


if __name__ == "__main__":
    main()
