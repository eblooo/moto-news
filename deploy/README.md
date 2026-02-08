# Деплой Moto-News на microk8s

**Целевой сервер:** HP ProDesk 600 G1 SFF — Intel Core i5-4590, 24 ГБ RAM, без GPU  
**ОС:** Ubuntu 24.04 LTS  
**Оркестратор:** microk8s

## Архитектура

```
┌─────────────────────────────────────────────────────────┐
│                    microk8s (namespace: moto-news)       │
│                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────┐  │
│  │   Ollama      │    │  Aggregator  │    │  Agents   │  │
│  │  StatefulSet  │◄───│  Deployment  │    │  CronJobs │  │
│  │              │    │              │    │           │  │
│  │ llama3.2:3b  │◄───│ Go HTTP API  │    │ Python    │  │
│  │ qwen2.5:7b   │    │ Port: 8080   │    │ LangChain │  │
│  │ deepseek:8b  │    │              │    │ LangGraph │  │
│  └──────┬───────┘    └──────┬───────┘    └─────┬─────┘  │
│         │                   │                   │        │
│         │            ┌──────┴───────┐           │        │
│         │            │  SQLite DB   │           │        │
│         │            │  PVC: 2Gi    │           │        │
│         │            └──────────────┘           │        │
│         │                                       │        │
│    ┌────┴────┐                          ┌──────┴──────┐ │
│    │ PVC:20Gi│                          │GitHub Disc. │ │
│    │ Models  │                          │(API)        │ │
│    └─────────┘                          └─────────────┘ │
└─────────────────────────────────────────────────────────┘
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

### 2. Включить необходимые аддоны

```bash
microk8s enable dns storage registry
```

- **dns** — внутренний DNS для k8s сервисов
- **storage** — динамическое выделение PersistentVolumes
- **registry** — локальный Docker registry (localhost:32000)

### 3. Установить Docker (для сборки образов)

```bash
sudo apt install docker.io
sudo usermod -aG docker $USER
```

## Быстрый деплой

```bash
# Клонировать репозиторий
git clone https://github.com/KlimDos/moto-news.git
cd moto-news

# Запустить деплой
./deploy/deploy.sh all
```

Скрипт автоматически:
1. Соберёт Docker-образы (aggregator + agents)
2. Запушит их в локальный registry microk8s
3. Создаст namespace `moto-news`
4. Задеплоит Ollama, скачает модели
5. Задеплоит Aggregator (Go HTTP API)
6. Настроит CronJobs для агентов

## Пошаговый деплой

### Шаг 1: Namespace

```bash
microk8s kubectl apply -f deploy/k8s/base/namespace.yaml
```

### Шаг 2: Ollama

```bash
# Деплой
microk8s kubectl apply -f deploy/k8s/ollama/

# Дождаться готовности
microk8s kubectl -n moto-news rollout status statefulset/ollama

# Загрузить модели (запуск Job)
microk8s kubectl apply -f deploy/k8s/ollama/init-models.yaml

# Проверить загрузку моделей
microk8s kubectl -n moto-news logs job/ollama-init-models -f
```

### Шаг 3: Aggregator

```bash
# Собрать образ
docker build -t localhost:32000/moto-news-aggregator:latest .
docker push localhost:32000/moto-news-aggregator:latest

# Деплой
microk8s kubectl apply -f deploy/k8s/aggregator/
```

### Шаг 4: Agents

```bash
# Собрать образ
docker build -t localhost:32000/moto-news-agents:latest -f agents/Dockerfile agents/
docker push localhost:32000/moto-news-agents:latest

# Создать секрет с GitHub токеном
microk8s kubectl -n moto-news create secret generic github-token \
  --from-literal=GITHUB_TOKEN=ghp_your_actual_token_here

# Деплой
microk8s kubectl apply -f deploy/k8s/agents/
```

## Проверка

```bash
# Статус всех ресурсов
microk8s kubectl -n moto-news get all

# Логи Ollama
microk8s kubectl -n moto-news logs statefulset/ollama -f

# Логи Aggregator
microk8s kubectl -n moto-news logs deployment/aggregator -f

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
docker build -t localhost:32000/moto-news-aggregator:latest .
docker push localhost:32000/moto-news-aggregator:latest

# Перезапустить deployment
microk8s kubectl -n moto-news rollout restart deployment/aggregator
```

## Удаление

```bash
microk8s kubectl delete namespace moto-news
```

## Файловая структура деплоя

```
deploy/
├── deploy.sh              # Скрипт автоматического деплоя
├── README.md              # Эта документация
└── k8s/
    ├── base/
    │   └── namespace.yaml
    ├── ollama/
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
