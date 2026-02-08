.PHONY: build run test docker-build docker-push deploy-all deploy-ollama deploy-aggregator deploy-agents agents-setup agents-test status clean

# ===== Go Aggregator =====

build:
	CGO_ENABLED=1 go build -o bin/aggregator ./cmd/aggregator/

run: build
	./bin/aggregator server

fetch: build
	./bin/aggregator fetch

translate: build
	./bin/aggregator translate --limit 5

publish: build
	./bin/aggregator publish

stats: build
	./bin/aggregator stats

# ===== Docker =====

docker-build:
	docker build -t localhost:32000/moto-news-aggregator:latest .
	docker build -t localhost:32000/moto-news-agents:latest -f agents/Dockerfile agents/

docker-push: docker-build
	docker push localhost:32000/moto-news-aggregator:latest
	docker push localhost:32000/moto-news-agents:latest

# ===== microk8s Deploy =====

deploy-all:
	./deploy/deploy.sh all

deploy-ollama:
	./deploy/deploy.sh ollama

deploy-aggregator:
	./deploy/deploy.sh aggregator

deploy-agents:
	./deploy/deploy.sh agents

status:
	./deploy/deploy.sh status

# ===== AI Agents (local development) =====

agents-setup:
	cd agents && python3 -m venv .venv && \
	. .venv/bin/activate && \
	pip install -r requirements.txt

agents-assess:
	cd agents && . .venv/bin/activate && \
	python site_assessor.py --url https://blog.alimov.top

agents-user-dry:
	cd agents && . .venv/bin/activate && \
	python user_agent.py --once --dry-run

agents-admin-dry:
	cd agents && . .venv/bin/activate && \
	python admin_agent.py --once --auto-approve

# ===== Cleanup =====

clean:
	rm -rf bin/
	rm -f moto-news.db
