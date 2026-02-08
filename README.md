# Moto News Aggregator

Автоматизированная система агрегации мотоновостей с переводом на русский язык и AI-агентами для анализа блога.

## Возможности

- RSS-парсинг мотоциклетных порталов (RideApart)
- Скрапинг полного текста статей (JSON-LD + HTML fallback)
- Перевод на русский через Ollama или LibreTranslate
- Публикация в блог на Hugo (PaperMod) через GitHub API
- HTTP API сервер (Gin) для управления через REST
- AI-агенты для анализа сайта и предложений по улучшению (LangChain + LangGraph)
- Деплой в Kubernetes (microk8s) через ArgoCD

## Быстрый старт

### Требования

- Go 1.23+
- Ollama (для перевода)
- `GITHUB_TOKEN` — Fine-grained PAT для публикации в блог

### Установка

```bash
git clone https://github.com/eblooo/moto-news.git
cd moto-news
go mod tidy
go build -o aggregator ./cmd/aggregator/
```

### Установка Ollama

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Скачать модель
ollama pull qwen2.5-coder:7b
```

### Запуск

```bash
# Установить токен для публикации в GitHub
export GITHUB_TOKEN=github_pat_xxxxx

# Запустить HTTP API сервер
./aggregator server

# Или полный цикл одной командой
./aggregator run
```

## HTTP API

```bash
./aggregator server   # Запуск на :8080
```

| Endpoint | Метод | Описание |
|---|---|---|
| `/api/fetch` | POST | Получить новые статьи из RSS |
| `/api/translate?limit=10` | POST | Перевести статьи через Ollama |
| `/api/publish?limit=100` | POST | Опубликовать в блог (GitHub API) |
| `/api/run` | POST | Полный цикл: fetch → translate → publish |
| `/api/rescrape` | POST | Повторно загрузить контент статей |
| `/api/pull` | POST | Git pull блог-репозитория |
| `/api/push` | POST | Git push изменений |
| `/api/stats` | GET | Статистика базы данных |
| `/api/articles?limit=20` | GET | Список статей |
| `/api/article/:id` | GET | Получить статью по ID |
| `/health` | GET | Health check |

Примеры:

```bash
curl -X POST http://localhost:8080/api/fetch
curl -X POST "http://localhost:8080/api/translate?limit=5"
curl -X POST http://localhost:8080/api/publish
curl http://localhost:8080/api/stats
```

## CLI команды

```bash
./aggregator fetch              # Получить новые статьи из RSS
./aggregator translate -l 20    # Перевести статьи
./aggregator publish            # Опубликовать в Hugo блог
./aggregator run                # Полный цикл
./aggregator rescrape           # Повторно скачать контент
./aggregator stats              # Статистика
./aggregator pull               # Git pull
./aggregator push               # Git push
./aggregator server             # HTTP API сервер
```

## Публикация статей

Поддерживаются два способа публикации:

### 1. GitHub API (рекомендуется)

Если установлен `GITHUB_TOKEN`, статьи пушатся напрямую через GitHub Contents API. Это автоматически триггерит GitHub Actions для деплоя на GitHub Pages.

Токен: **Fine-grained PAT** с правами:
- Repository: `KlimDos/my-blog` only
- Permissions: Contents → Read and write

```bash
export GITHUB_TOKEN=github_pat_xxxxx
```

### 2. Локальный git (fallback)

Если `GITHUB_TOKEN` не установлен, статьи записываются в локальную директорию и коммитятся через `git`. Требует клонированный репозиторий блога и настроенные git credentials.

## Конфигурация

`config.yaml`:

```yaml
sources:
  - name: rideapart
    feeds:
      - https://www.rideapart.com/rss/news/all/
      - https://www.rideapart.com/rss/reviews/all/
      - https://www.rideapart.com/rss/features/all/
    enabled: true

translator:
  provider: ollama
  ollama:
    model: qwen2.5-coder:7b
    host: http://localhost:11434
    prompt: |
      You are a professional English to Russian translator...

database:
  path: ./moto-news.db

hugo:
  path: ./blog
  content_dir: content
  auto_commit: true
  git_repo: https://github.com/KlimDos/my-blog.git
  git_branch: main

server:
  host: 0.0.0.0
  port: 8080

schedule:
  fetch_interval: 6h
  translate_batch: 5
```

## AI-агенты

Python-агенты для анализа блога и взаимодействия через GitHub Discussions.

```bash
cd agents
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

| Агент | Описание | Запуск |
|---|---|---|
| `site_assessor.py` | Анализ блога, генерация отчёта | `python site_assessor.py --url https://blog.alimov.top` |
| `user_agent.py` | ReAct-агент, пишет предложения в GitHub Discussions | `python user_agent.py --once --dry-run` |
| `admin_agent.py` | LangGraph workflow с human approval | `python admin_agent.py --once` |

Подробнее: см. `agents/agents.yaml` для настройки моделей и параметров.

## Структура проекта

```
moto-news/
├── cmd/aggregator/        # CLI + точка входа
├── internal/
│   ├── config/            # Конфигурация (Viper)
│   ├── fetcher/           # RSS парсер + скрапер
│   ├── models/            # Модели данных (Article)
│   ├── storage/           # SQLite хранилище
│   ├── translator/        # Ollama / LibreTranslate
│   ├── formatter/         # Markdown форматирование
│   ├── publisher/         # GitHub API + Hugo git (fallback)
│   ├── service/           # Бизнес-логика
│   └── server/            # Gin HTTP API
├── agents/                # Python AI-агенты (LangChain/LangGraph)
├── deploy/                # K8s манифесты + скрипты деплоя
├── blog/                  # Hugo сайт (отдельный репо)
├── Dockerfile             # Multi-stage build для Go
├── Makefile               # Команды сборки и деплоя
└── config.yaml            # Конфигурация
```

## Деплой в Kubernetes (microk8s)

Подробная документация: [`deploy/README.md`](deploy/README.md)

Архитектура:
- **Ollama** — на хосте (systemd), доступна из K8s через Endpoints
- **Aggregator** — Deployment + CronJob (каждые 6ч)
- **AI Agents** — CronJobs (user-agent каждые 2ч, site-assessor ежедневно)
- **Ingress** — NGINX + Let's Encrypt на `moto-news.alimov.top`
- **Secrets** — ExternalSecrets (Doppler)

```bash
# Docker сборка
make docker-build

# Или через Makefile
make deploy-all
```

## Лицензия

MIT
