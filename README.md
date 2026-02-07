# Moto News Aggregator

Автоматизированная система агрегации мотоновостей с переводом на русский язык.

## Возможности

- 📡 Парсинг RSS-фидов с мотоциклетных порталов (RideApart)
- 🔄 Скрапинг полного текста статей
- 🌐 Перевод на русский через Ollama или LibreTranslate
- 📝 Публикация в блог на Material for MkDocs
- 🔧 Автоматический git commit/push

## Быстрый старт

### Требования

- Go 1.21+
- Ollama (опционально, для перевода)
- Material for MkDocs (для блога)

### Установка

```bash
# Клонируйте репозиторий
git clone https://github.com/your/moto-news.git
cd moto-news

# Установите зависимости
go mod tidy

# Соберите приложение
go build -o aggregator ./cmd/aggregator/

# Отредактируйте конфигурацию
cp config.yaml.example config.yaml
nano config.yaml
```

### Установка Ollama (для перевода)

```bash
# macOS
brew install ollama

# Запустите сервер
ollama serve

# Скачайте модель
ollama pull gemma2:9b
```

### Установка MkDocs

```bash
pip install mkdocs-material
pip install mkdocs-blog-plugin
```

## Использование

### Команды CLI

```bash
# Получить новые статьи из RSS
./aggregator fetch

# Перевести статьи (по умолчанию 10)
./aggregator translate --limit 20

# Опубликовать в MkDocs
./aggregator publish

# Полный цикл
./aggregator run

# Статистика
./aggregator stats
```

### Опции

```bash
./aggregator --help
./aggregator translate --help
```

## Конфигурация

Создайте `config.yaml`:

```yaml
sources:
  - name: rideapart
    feeds:
      - https://www.rideapart.com/rss/news/all/
      - https://www.rideapart.com/rss/reviews/all/
    enabled: true

translator:
  provider: ollama  # или "libretranslate"
  ollama:
    model: gemma2:9b
    host: http://localhost:11434

database:
  path: ./moto-news.db

mkdocs:
  path: ./blog
  docs_dir: docs/news
  auto_commit: true
```

## Автоматизация

### Cron (ежедневный запуск)

```bash
# Откройте crontab
crontab -e

# Добавьте строку (каждый день в 8:00)
0 8 * * * cd /path/to/moto-news && ./aggregator run >> /var/log/moto-news.log 2>&1
```

### systemd (Linux)

Создайте `/etc/systemd/system/moto-news.service`:

```ini
[Unit]
Description=Moto News Aggregator
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/path/to/moto-news
ExecStart=/path/to/moto-news/aggregator run
User=your-user

[Install]
WantedBy=multi-user.target
```

Создайте `/etc/systemd/system/moto-news.timer`:

```ini
[Unit]
Description=Run Moto News Aggregator daily

[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Активируйте:

```bash
sudo systemctl enable moto-news.timer
sudo systemctl start moto-news.timer

# Проверить статус
sudo systemctl status moto-news.timer
sudo systemctl list-timers
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
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USER/moto-news</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USER/moto-news/logs/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USER/moto-news/logs/stderr.log</string>
</dict>
</plist>
```

Активируйте:

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

## Структура проекта

```
moto-news/
├── cmd/aggregator/     # CLI приложение
├── internal/
│   ├── config/         # Конфигурация
│   ├── fetcher/        # RSS и скрапер
│   ├── models/         # Модели данных
│   ├── storage/        # SQLite
│   ├── translator/     # Ollama/LibreTranslate
│   ├── formatter/      # Markdown
│   └── publisher/      # MkDocs + Git
├── blog/               # MkDocs сайт
├── config.yaml         # Конфигурация
└── moto-news.db        # База данных (создаётся автоматически)
```

## Лицензия

MIT
