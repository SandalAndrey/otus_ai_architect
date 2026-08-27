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
class Usage:
    calls: int = 0
    seconds: float = 0.0
    by_role: dict[str, float] = field(default_factory=dict)

    def add(self, role: str, dt: float) -> None:
        self.calls += 1
        self.seconds += dt
        self.by_role[role] = self.by_role.get(role, 0.0) + dt


class BaseLLM:
    def __init__(self) -> None:
        self.usage = Usage()

    def complete(self, role: str, system: str, user: str) -> str:
        t0 = time.perf_counter()
        try:
            return self._call(role, system, user)
        finally:
            self.usage.add(role, time.perf_counter() - t0)

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

    def __init__(self, base_url: str, model: str, timeout: float = 180.0) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _call(self, role: str, system: str, user: str) -> str:
        # /no_think выключает режим рассуждений у гибридных моделей Qwen3.
        # Без него на CPU время ответа вырастает в разы, и замер профиля A
        # перестаёт что-либо означать.
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system + "\n/no_think"},
                {"role": "user", "content": user},
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
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def build_llm() -> BaseLLM:
    provider = os.getenv("LLM_PROVIDER", "stub").strip().lower()
    if provider == "stub":
        return StubLLM()
    return OllamaLLM(
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
        model=os.getenv("LLM_MODEL", "qwen3-8b"),
        timeout=float(os.getenv("LLM_TIMEOUT", "180")),
    )
