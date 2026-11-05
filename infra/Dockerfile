# Образ приложения и агентов. Описание - docs/16-delivery-pipeline.md.
#
# СКЕЛЕТ. Каталога backend/app пока нет, поэтому образ не собирается. Файл
# зафиксирован, чтобы решения о его устройстве были приняты до написания кода,
# а не подогнаны под него. Собирается начиная с работы 3.1 плана работ.

FROM python:3.12-slim AS base

# Версия интерпретатора закреплена намеренно. Ragas тянет зависимости, которые
# отстают от свежих версий Python; обновление интерпретатора способно уронить
# оценку качества, не затронув наш код. Это уже случалось при замере судьи.

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Зависимости отдельным слоем: они меняются реже кода.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# Приложение работает не от root: образ попадает в контур, где лежат
# хранилища с данными под грифом Confidential.
RUN useradd --system --uid 10001 assistant
USER assistant

# Веса моделей в образ не кладутся: они монтируются томом и версионируются
# отдельно, запись реестра выпуска - infra/release/.
# Это же не даёт образу раздуться до десятков гигабайт.

EXPOSE 8000

# Проверка живости нужна обратному прокси: при канареечном выпуске он должен
# отличать "версия поднимается" от "версия сломана".
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
