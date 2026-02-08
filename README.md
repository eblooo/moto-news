# Moto News Aggregator

Автоматизированная система агрегации мотоновостей с переводом на русский язык.

## Возможности

- 📡 Парсинг RSS-фидов с мотоциклетных порталов (RideApart)
- 🔄 Скрапинг полного текста статей (JSON-LD + HTML fallback)
- 🌐 Перевод на русский через Ollama или LibreTranslate
- 📝 Публикация в блог на Material for MkDocs
- 🔧 Автоматический git commit/push
- 🌍 HTTP API сервер (Gin) для управления через REST

## Быстрый старт

### Требования

- Go 1.23+
- Ollama (для перевода)
- Material for MkDocs (для блога)

### Установка

```bash
# Клонируйте репозиторий
git clone https://github.com/KlimDos/moto-news.git
cd moto-news

# Установите зависимости
go mod tidy

# Соберите приложение
go build -o aggregator ./cmd/aggregator/

# Отредактируйте конфигурацию
nano config.yaml
```

### Установка Ollama (для перевода)

```bash
# macOS
brew install ollama

# Запустите сервер
ollama serve

# Скачайте модель
ollama pull gemma3:latest
```

### Установка MkDocs

```bash
pip install mkdocs-material
pip install mkdocs-blog-plugin
```

## Использование

### HTTP API сервер (рекомендуется)

```bash
# Запустить веб-сервер на :8080
./aggregator server
```

Все операции доступны через REST API:

| Endpoint | Метод | Описание |
|---|---|---|
| `/api/fetch` | POST | Получить новые статьи из RSS |
| `/api/translate?limit=10` | POST | Перевести статьи через Ollama |
| `/api/publish?limit=100` | POST | Опубликовать в MkDocs |
| `/api/run` | POST | Полный цикл: fetch → translate → publish |
| `/api/rescrape` | POST | Повторно загрузить контент коротких статей |
| `/api/pull` | POST | Git pull блог-репозитория |
| `/api/push` | POST | Git push изменений |
| `/api/stats` | GET | Статистика базы данных |
| `/api/articles?limit=20` | GET | Список статей |
| `/api/article/:id` | GET | Получить статью по ID |
| `/health` | GET | Проверка здоровья сервера |

Примеры:

```bash
# Получить новые статьи
curl -X POST http://localhost:8080/api/fetch

# Перевести 5 статей
curl -X POST "http://localhost:8080/api/translate?limit=5"

# Полный цикл
curl -X POST http://localhost:8080/api/run

# Статистика
curl http://localhost:8080/api/stats

# Список последних статей
curl "http://localhost:8080/api/articles?limit=10"
```

### Команды CLI

```bash
# Получить новые статьи из RSS
./aggregator fetch

# Перевести статьи (по умолчанию 10)
./aggregator translate --limit 20

# Опубликовать в MkDocs
./aggregator publish

# Полный цикл: fetch -> translate -> publish
./aggregator run

# Повторно скачать контент для статей с коротким текстом
./aggregator rescrape

# Статистика
./aggregator stats

# Git операции
./aggregator pull
./aggregator push

# Запустить HTTP API сервер
./aggregator server

# Помощь
./aggregator --help
```

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
  provider: ollama  # или "libretranslate"
  ollama:
    model: gemma3:latest
    host: http://localhost:11434
    prompt: |
      Переведи следующую статью о мотоциклах на русский язык.
      Сохрани технические термины и названия моделей мотоциклов на английском.
      Используй профессиональную мотожурналистскую стилистику.
      Не добавляй никаких комментариев, верни только перевод.

      Статья:
  libretranslate:
    host: http://localhost:5050

database:
  path: ./moto-news.db

mkdocs:
  path: ./blog
  docs_dir: docs
  auto_commit: true
  git_repo: https://github.com/KlimDos/my-blog.git
  git_remote: origin
  git_branch: main

server:
  host: 0.0.0.0
  port: 8080

schedule:
  fetch_interval: 6h
  translate_batch: 10
```

## Структура проекта

```
moto-news/
├── cmd/aggregator/        # CLI + точка входа
├── internal/
│   ├── config/            # Конфигурация (Viper)
│   ├── fetcher/           # RSS парсер + скрапер (JSON-LD / HTML)
│   ├── models/            # Модели данных (Article)
│   ├── storage/           # SQLite хранилище
│   ├── translator/        # Ollama / LibreTranslate
│   ├── formatter/         # Markdown форматирование
│   ├── publisher/         # MkDocs + Git операции
│   ├── service/           # Бизнес-логика (общая для CLI и API)
│   └── server/            # Gin HTTP API сервер
├── blog/                  # MkDocs сайт
├── config.yaml            # Конфигурация
└── moto-news.db           # SQLite база (создаётся автоматически)
```

## Автоматизация

### Cron (ежедневный запуск)

```bash
crontab -e

# Каждый день в 8:00
0 8 * * * cd /path/to/moto-news && ./aggregator run >> /var/log/moto-news.log 2>&1
```

### systemd (Linux)

Создайте `/etc/systemd/system/moto-news.service` для запуска HTTP сервера:

```ini
[Unit]
Description=Moto News Aggregator API
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/moto-news
ExecStart=/path/to/moto-news/aggregator server
Restart=always
User=your-user

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable moto-news
sudo systemctl start moto-news
```

### launchd (macOS)

Создайте `~/Library/LaunchAgents/com.moto-news.aggregator.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.moto-news.aggregator</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USER/moto-news/aggregator</string>
        <string>server</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USER/moto-news</string>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USER/moto-news/logs/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USER/moto-news/logs/stderr.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.moto-news.aggregator.plist
```

## Запуск MkDocs

```bash
cd blog

# Локальный сервер для разработки
mkdocs serve

# Сборка статического сайта
mkdocs build

# Деплой на GitHub Pages
mkdocs gh-deploy
```

## Лицензия

MIT
