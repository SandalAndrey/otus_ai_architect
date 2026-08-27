"""Единственная точка обращения к модели.

Соответствует компоненту "Клиент инференса" на диаграмме уровня 3: агенты
обращаются к модели только отсюда. Параметр role передаётся с прицелом на
будущее разделение моделей по ролям (ADR-0007, раздел "Последствия"): пока
модель одна, но интерфейс уже готов.

Два исполнения. StubLLM отвечает детерминированно и без внешних зависимостей -
на нём проверяется логика передач между агентами. OllamaLLM ходит в локальный
OpenAI-совместимый endpoint; наружу контура запросы не уходят.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class RoleStat:
    calls: int = 0
    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_calls: int = 0     # ответов, где модель всё-таки рассуждала


@dataclass
class Usage:
    """Учёт вызовов по ролям.

    Токены считаются не ради статистики, а чтобы отличить "модель отвечала
    коротко и медленно" от "модель рассуждала". Без этого числа вывод о
    латентности повисает: 13 секунд на короткий JSON означают либо тяжёлую
    модель, либо невыключенный режим рассуждений, и по времени их не различить.
    """

    calls: int = 0
    seconds: float = 0.0
    by_role: dict[str, RoleStat] = field(default_factory=dict)

    def add(self, role: str, dt: float, prompt: int = 0, completion: int = 0,
            thinking: bool = False) -> None:
        self.calls += 1
        self.seconds += dt
        st = self.by_role.setdefault(role, RoleStat())
        st.calls += 1
        st.seconds += dt
        st.prompt_tokens += prompt
        st.completion_tokens += completion
        st.thinking_calls += int(thinking)

    def report(self) -> str:
        out = [f"{'роль':<12}{'вызовов':>8}{'сек':>8}{'сек/выз':>9}"
               f"{'ток.вх':>8}{'ток.вых':>9}{'ток/с':>7}{'думала':>8}"]
        for role, st in sorted(self.by_role.items(), key=lambda x: -x[1].seconds):
            tps = st.completion_tokens / st.seconds if st.seconds else 0
            out.append(f"{role:<12}{st.calls:>8}{st.seconds:>8.1f}"
                       f"{st.seconds / max(st.calls, 1):>9.1f}"
                       f"{st.prompt_tokens:>8}{st.completion_tokens:>9}"
                       f"{tps:>7.1f}{st.thinking_calls:>8}")
        return "\n".join(out)


class BaseLLM:
    def __init__(self) -> None:
        self.usage = Usage()

    def complete(self, role: str, system: str, user: str) -> str:
        t0 = time.perf_counter()
        self._last = {}
        try:
            return self._call(role, system, user)
        finally:
            m = getattr(self, "_last", {}) or {}
            self.usage.add(role, time.perf_counter() - t0,
                           m.get("prompt_tokens", 0), m.get("completion_tokens", 0),
                           m.get("thinking", False))

    def _call(self, role: str, system: str, user: str) -> str:
        raise NotImplementedError

    def json(self, role: str, system: str, user: str, default: dict) -> dict:
        """Разбор ответа модели как JSON с откатом на значение по умолчанию.

        Модель может вернуть текст вокруг JSON или невалидный JSON. Падать
        из-за этого нельзя: отказ агента не должен ронять весь запрос.
        """
        raw = self.complete(role, system + "\nОтвечай только JSON, без пояснений.", user)
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return default
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return default
        return parsed if isinstance(parsed, dict) else default


class StubLLM(BaseLLM):
    """Детерминированная заглушка: правила вместо модели.

    Нужна не для экономии, а для отладки: маршрут агентов и фильтр ACL
    проверяются воспроизводимо, без влияния вероятностного вывода.
    """

    MULTIHOP = ("ещё", "еще", "другие", "перепак", "пресс-форм", "аналог",
                "отлич", "сравн", "какие бренды", "кто ещё", "кто еще")

    def _call(self, role: str, system: str, user: str) -> str:
        low = user.lower()
        if role == "supervisor":
            klass = "B" if any(w in low for w in self.MULTIHOP) else "A"
            return json.dumps({"class": klass,
                               "need_graph": klass == "B",
                               "reason": "заглушка: по ключевым словам"},
                              ensure_ascii=False)
        if role == "vector":
            return json.dumps({"queries": [user]}, ensure_ascii=False)
        if role == "graph":
            return json.dumps({"depth": 2 if "перепак" in low or "пресс-форм" in low else 1},
                              ensure_ascii=False)
        if role == "composer":
            facts = user.split("ФАКТЫ:", 1)[-1].strip()
            first = facts.split("\n")[0][:300] if facts else ""
            return f"По имеющимся данным: {first}"
        if role == "verifier":
            return json.dumps({"supported": True, "note": "заглушка не сверяет"},
                              ensure_ascii=False)
        return ""


class OllamaLLM(BaseLLM):
    """Локальный OpenAI-совместимый endpoint (Ollama, llama.cpp, vLLM).

    Обращений наружу контура нет - это ограничение проекта, а не настройка.
    """

    def __init__(self, base_url: str, model: str, timeout: float = 180.0,
                 by_role: dict[str, str] | None = None) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # Разные модели под разные роли. Классификация и выбор глубины обхода -
        # короткий JSON, там хватает модели поменьше; качество нужно только
        # составителю ответа. Заложено в ADR-0007, проверяется замером.
        self.by_role = by_role or {}

    def model_for(self, role: str) -> str:
        return self.by_role.get(role, self.model)

    def _call(self, role: str, system: str, user: str) -> str:
        # /no_think выключает режим рассуждений у гибридных моделей Qwen3.
        # Без него на CPU время ответа вырастает в разы, и замер профиля A
        # перестаёт что-либо означать.
        payload = {
            "model": self.model_for(role),
            "messages": [
                {"role": "system", "content": system + "\n/no_think"},
                # Переключатель дублируется в пользовательский ход: Qwen
                # документирует его прежде всего для него, и в системном
                # промпте он срабатывает не всегда.
                {"role": "user", "content": user + "\n/no_think"},
            ],
            "temperature": 0,
            "stream": False,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer local"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Сервис инференса недоступен: {exc}") from exc
        text = body["choices"][0]["message"]["content"]
        # Непустой блок <think> означает, что переключатель /no_think не
        # сработал. Отличить это по времени ответа невозможно, поэтому факт
        # фиксируется явно.
        thought = re.search(r"<think>(.*?)</think>", text, flags=re.S)
        usage = body.get("usage") or {}
        self._last = {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "thinking": bool(thought and thought.group(1).strip()),
        }
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def build_llm() -> BaseLLM:
    provider = os.getenv("LLM_PROVIDER", "stub").strip().lower()
    if provider == "stub":
        return StubLLM()
    roles = ("supervisor", "vector", "graph", "composer", "verifier")
    by_role = {r: os.environ[f"LLM_MODEL_{r.upper()}"]
               for r in roles if os.getenv(f"LLM_MODEL_{r.upper()}")}
    return OllamaLLM(
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
        model=os.getenv("LLM_MODEL", "qwen3-8b"),
        timeout=float(os.getenv("LLM_TIMEOUT", "180")),
        by_role=by_role,
    )
