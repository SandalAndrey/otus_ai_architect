#!/usr/bin/env python3
"""Проверка и замена типографских символов в файлах репозитория.

Правило проекта: в документации и в документировании кода используются только
основные символы с клавиатуры. Типографские знаки ломают grep, дают шум в diff
и по-разному отображаются в терминалах.

Запуск:
    python tools/check_punctuation.py          проверить, вернуть код 1 при находках
    python tools/check_punctuation.py --fix    заменить на месте
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

REPLACEMENTS = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    "«": '"', "»": '"',
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "…": "...",
    "→": "->", "←": "<-", "⇒": "=>",
    "×": "x", "±": "+/-",
    " ": " ", " ": " ", " ": " ", " ": " ",
    "✓": "да", "✗": "нет",
    "•": "-", "­": "",
    "≤": "<=", "≥": ">=", "≠": "!=", "≈": "~",
    "©": "(c)", "™": "", "®": "",
}

SUFFIXES = {".md", ".py", ".yml", ".yaml", ".cypher", ".toml", ".cfg", ".txt", ".sh"}
EXTRA_FILES = {"Makefile", "Dockerfile", ".gitignore", ".dockerignore", ".env.example"}
SKIP_DIRS = {".git", "docs/pdf", "__pycache__", ".venv", "node_modules"}
# Скрипт содержит таблицу замен и не должен обрабатывать сам себя.
SKIP_FILES = {"tools/check_punctuation.py"}


def is_allowed(ch: str) -> bool:
    """Разрешены ASCII и кириллица. Всё прочее требует внимания."""
    if ch in ("\n", "\t"):
        return True
    if " " <= ch <= "~":
        return True
    return "Ѐ" <= ch <= "ӿ"


def collect(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS):
            continue
        if rel in SKIP_FILES:
            continue
        if path.suffix in SUFFIXES or path.name in EXTRA_FILES:
            files.append(path)
    return files


def process(path: Path, fix: bool) -> tuple[int, set[str]]:
    text = path.read_text(encoding="utf-8")
    replaced = sum(text.count(src) for src in REPLACEMENTS)
    fixed = text
    for src, dst in REPLACEMENTS.items():
        fixed = fixed.replace(src, dst)
    unknown = {ch for ch in fixed if not is_allowed(ch)}
    if fix and replaced:
        path.write_text(fixed, encoding="utf-8")
    return replaced, unknown


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="заменить на месте")
    parser.add_argument("--root", default=".", help="корень репозитория")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    total, problems = 0, False

    for path in collect(root):
        count, unknown = process(path, args.fix)
        rel = path.relative_to(root)
        if count:
            total += count
            problems = problems or not args.fix
            action = "исправлено" if args.fix else "найдено"
            print(f"{rel}: {action} {count}")
        for ch in sorted(unknown):
            problems = True
            name = unicodedata.name(ch, "без имени")
            print(f"{rel}: неизвестный символ {ch!r} (U+{ord(ch):04X}, {name})")

    if not total and not problems:
        print("Типографских символов не найдено.")
    elif args.fix:
        print(f"Всего заменено: {total}")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
