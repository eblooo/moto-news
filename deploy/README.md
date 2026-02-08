# Деплой Moto-News на microk8s

**Целевой сервер:** HP ProDesk 600 G1 SFF — Intel Core i5-4590, 24 ГБ RAM, без GPU  
**ОС:** Ubuntu 24.04 LTS  
**Оркестратор:** microk8s

## Архитектура

```
  ┌─────────────────┐
  │  Ollama (host)   │   ← запущена на хосте, не в K8s
  │  llama3.2:3b     │
  │  qwen2.5:7b      │
  │  deepseek:8b     │
  │  :11434          │
  └────────┬─────────┘
           │ Endpoints → 172.16.0.164:11434
┌──────────┼──────────────────────────────────────────┐
│  microk8s│(namespace: moto-news-ns)                  │
│          │                                           │
│  ┌───────┴──────┐    ┌───────────┐                  │
│  │  Aggregator  │    │  Agents   │                  │
│  │  Deployment  │    │  CronJobs │                  │
│  │              │    │           │                  │
│  │ Go HTTP API  │    │ Python    │                  │
│  │ Port: 8080   │    │ LangChain │                  │
│  │              │    │ LangGraph │                  │
│  └──────┬───────┘    └─────┬─────┘                  │
│         │                   │                        │
│  ┌──────┴───────┐   ┌──────┴──────┐                 │
│  │  SQLite DB   │   │GitHub Disc. │                 │
│  │  hostPath    │   │(API)        │                 │
│  └──────────────┘   └─────────────┘                 │
│         │                                            │
│  ┌──────┴───────┐                                   │
│  │ GitHub API   │ ← GITHUB_TOKEN (ExternalSecret)   │
│  │ Contents API │ → auto-triggers GH Actions        │
│  └──────────────┘                                   │
└─────────────────────────────────────────────────────┘
         │                      │
         ▼                      ▼
   blog.alimov.top       GitHub Pages
   (MkDocs Material)     (auto-deploy)
```

## Подготовка сервера

### 1. Установить microk8s (если ещё не установлен)

```bash
sudo snap install microk8s --classic
sudo usermod -aG microk8s $USER
newgrp microk8s
```

### 2. Установить и настроить Ollama

```bash
# Установка
curl -fsSL https://ollama.com/install.sh | sh

# Настроить прослушивание на всех интерфейсах (для K8s pods)
sudo mkdir -p /etc/systemd/system/ollama.service.d
echo -e '[Service]\nEnvironment=OLLAMA_HOST=0.0.0.0' | \
    sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama

# Загрузить модели
ollama pull llama3.2:3b
ollama pull qwen2.5-coder:7b
ollama pull deepseek-r1:8b
```

### 3. Включить необходимые аддоны

```bash
microk8s enable dns storage registry
```

- **dns** — внутренний DNS для k8s сервисов
- **storage** — динамическое выделение PersistentVolumes
- **registry** — (опционально) локальный Docker registry

### 4. Установить Docker (для сборки образов)

```bash
sudo apt install docker.io
sudo usermod -aG docker $USER
```

## Быстрый деплой

```bash
# Клонировать репозиторий
git clone https://github.com/eblooo/moto-news.git
cd moto-news

# Запустить деплой (standalone, без ArgoCD)
./deploy/deploy.sh all
```

Скрипт автоматически:
1. Соберёт Docker-образы (aggregator + agents)
2. Запушит их в локальный registry microk8s
3. Создаст namespace
4. Задеплоит Aggregator (Go HTTP API)
5. Настроит CronJobs для агентов

> **Примечание:** Для production-деплоя используется ArgoCD через репозиторий `home-k8s`.
> Манифесты находятся в `home-k8s/application-data/yaml-local/moto-news/`.
> Секреты управляются через ExternalSecrets (Doppler).

## Пошаговый деплой

### Шаг 1: Namespace

```bash
microk8s kubectl apply -f deploy/k8s/base/namespace.yaml
```

### Шаг 2: Ollama (на хосте)

```bash
# Убедиться что Ollama запущена
systemctl status ollama

# Загрузить модели (если ещё не загружены)
./deploy/deploy.sh models
```

K8s pods обращаются к Ollama через Service `ollama-host-svc` → Endpoints `172.16.0.164:11434`.

### Шаг 3: Aggregator

```bash
# Собрать образ
docker build -t klimdos/moto-news-aggregator:latest .
docker push klimdos/moto-news-aggregator:latest

# Деплой
microk8s kubectl apply -f deploy/k8s/aggregator/
```

### Шаг 4: Agents

```bash
# Собрать образ
docker build -t klimdos/moto-news-agents:latest -f agents/Dockerfile agents/
docker push klimdos/moto-news-agents:latest

# Секрет с GitHub токеном (через ExternalSecrets / Doppler)
# Ключ MOTO_NEWS_GITHUB_TOKEN в Doppler → создаётся автоматически как git-credentials

# Деплой
microk8s kubectl apply -f deploy/k8s/agents/
```

## Проверка

```bash
# Статус всех ресурсов
microk8s kubectl -n moto-news-ns get all

# Логи Ollama (на хосте)
sudo journalctl -u ollama -f

# Логи Aggregator
microk8s kubectl -n moto-news-ns logs deployment/aggregator -f

# Health check
curl http://localhost:30080/health

# Статистика
curl http://localhost:30080/api/stats

# Запустить пайплайн вручную
curl -X POST http://localhost:30080/api/run
```

## Рекомендации для CPU-only (24 ГБ RAM)

| Модель | RAM | Скорость (tok/s) | Назначение |
|--------|-----|-------------------|------------|
| llama3.2:3b | ~2 ГБ | 15-30 | User-агент (быстрый) |
| qwen2.5-coder:7b | ~4.5 ГБ | 8-15 | Перевод, анализ кода |
| deepseek-r1:8b | ~5 ГБ | 5-12 | Admin-агент (рассуждения) |

**Важно:** На CPU без GPU перевод одной статьи может занимать 5-15 минут.
`translate_batch` установлен на 5 (в ConfigMap), чтобы не перегружать систему.

## Обновление

```bash
# Пересобрать и обновить образы
docker build -t klimdos/moto-news-aggregator:latest .
docker push klimdos/moto-news-aggregator:latest

# Перезапустить deployment
microk8s kubectl -n moto-news-ns rollout restart deployment/aggregator
```

## Удаление

```bash
microk8s kubectl delete namespace moto-news-ns
```

## Файловая структура деплоя

```
deploy/
├── deploy.sh              # Скрипт автоматического деплоя
├── README.md              # Эта документация
└── k8s/
    ├── base/
    │   └── namespace.yaml
    ├── ollama/                     # (опционально, если Ollama в K8s)
    │   ├── statefulset.yaml    # Ollama с PVC для моделей
    │   ├── service.yaml        # ClusterIP сервис
    │   └── init-models.yaml    # Job для загрузки моделей
    ├── aggregator/
    │   ├── deployment.yaml     # Go HTTP API сервер
    │   ├── service.yaml        # ClusterIP + NodePort:30080
    │   ├── configmap.yaml      # config.yaml
    │   ├── pvc.yaml            # SQLite DB + blog data
    │   └── cronjob.yaml        # Периодический пайплайн (каждые 6ч)
    └── agents/
        ├── configmap.yaml          # agents.yaml
        ├── secret.yaml             # GitHub token (шаблон)
        ├── cronjob-user-agent.yaml # User-агент (каждые 2ч)
        └── cronjob-site-assessor.yaml  # Оценка сайта (ежедневно)
```
