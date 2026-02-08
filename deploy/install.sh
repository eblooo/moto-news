#!/bin/bash
# ===================================================
# Full install of moto-news stack on microk8s
# Run this on the server (Ubuntu)
#
# Usage:
#   curl -sSL <raw-url> | bash
#   or:
#   bash install.sh [INSTALL_DIR]
#
# Default: /home/sasha/ssd500
# ===================================================

set -euo pipefail

INSTALL_DIR="${1:-/home/sasha/ssd500}"
REPO_URL="https://github.com/eblooo/moto-news.git"
PROJECT_DIR="$INSTALL_DIR/moto-news"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

echo ""
echo "=============================================="
echo "  Moto-News Full Install"
echo "  Target: $INSTALL_DIR"
echo "=============================================="
echo ""

# ===== Step 1: Install Ollama =====
log_info "Step 1: Installing Ollama..."

if command -v ollama &> /dev/null; then
    log_ok "Ollama already installed: $(ollama --version 2>/dev/null || echo 'unknown')"
else
    log_info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    log_ok "Ollama installed"
fi

# Configure Ollama to listen on all interfaces
log_info "Configuring Ollama to listen on 0.0.0.0..."
sudo mkdir -p /etc/systemd/system/ollama.service.d
cat <<'OVERRIDE' | sudo tee /etc/systemd/system/ollama.service.d/override.conf > /dev/null
[Service]
Environment=OLLAMA_HOST=0.0.0.0
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_MAX_LOADED_MODELS=1
OVERRIDE
sudo systemctl daemon-reload
sudo systemctl enable ollama
sudo systemctl restart ollama

# Wait for Ollama to start
log_info "Waiting for Ollama to start..."
for i in $(seq 1 30); do
    if curl -s http://localhost:11434/ > /dev/null 2>&1; then
        log_ok "Ollama is running"
        break
    fi
    sleep 2
done

# ===== Step 2: Pull models =====
log_info "Step 2: Pulling AI models (this will take a while)..."

for model in "llama3.2:3b" "qwen2.5-coder:7b"; do
    log_info "Pulling $model ..."
    ollama pull "$model"
    log_ok "$model ready"
done

log_info "Available models:"
ollama list

# ===== Step 3: Install Docker =====
log_info "Step 3: Checking Docker..."

if command -v docker &> /dev/null; then
    log_ok "Docker already installed"
else
    log_info "Installing Docker..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker.io
    sudo usermod -aG docker "$USER"
    log_ok "Docker installed (re-login may be needed for group permissions)"
fi

# ===== Step 4: Check microk8s =====
log_info "Step 4: Checking microk8s..."

if ! command -v microk8s &> /dev/null; then
    log_error "microk8s not found!"
    log_error "Install: sudo snap install microk8s --classic"
    exit 1
fi

log_info "Enabling required addons..."
microk8s enable dns storage registry 2>/dev/null || true
log_ok "microk8s ready"

# ===== Step 5: Clone repository =====
log_info "Step 5: Cloning repository to $PROJECT_DIR ..."

mkdir -p "$INSTALL_DIR"

if [ -d "$PROJECT_DIR" ]; then
    log_info "Directory exists, pulling latest..."
    cd "$PROJECT_DIR"
    git pull
else
    git clone "$REPO_URL" "$PROJECT_DIR"
    cd "$PROJECT_DIR"
fi

log_ok "Repository ready at $PROJECT_DIR"

# ===== Step 6: Build Docker images =====
log_info "Step 6: Building Docker images..."

# Build aggregator
log_info "Building aggregator image..."
docker build -t localhost:32000/moto-news-aggregator:latest -f Dockerfile . 2>&1 | tail -5
docker push localhost:32000/moto-news-aggregator:latest
log_ok "Aggregator image built and pushed"

# Build agents
log_info "Building agents image..."
docker build -t localhost:32000/moto-news-agents:latest -f agents/Dockerfile agents/ 2>&1 | tail -5
docker push localhost:32000/moto-news-agents:latest
log_ok "Agents image built and pushed"

# ===== Step 7: Deploy to microk8s =====
log_info "Step 7: Deploying to microk8s..."

# Namespace
microk8s kubectl apply -f deploy/k8s/base/namespace.yaml

# Aggregator
microk8s kubectl apply -f deploy/k8s/aggregator/configmap.yaml
microk8s kubectl apply -f deploy/k8s/aggregator/pvc.yaml
microk8s kubectl apply -f deploy/k8s/aggregator/service.yaml
sed 's|moto-news-aggregator:latest|localhost:32000/moto-news-aggregator:latest|g' \
    deploy/k8s/aggregator/deployment.yaml | microk8s kubectl apply -f -
microk8s kubectl apply -f deploy/k8s/aggregator/cronjob.yaml

log_info "Waiting for aggregator to be ready..."
microk8s kubectl -n moto-news rollout status deployment/aggregator --timeout=120s
log_ok "Aggregator deployed"

# Agents
microk8s kubectl apply -f deploy/k8s/agents/configmap.yaml
for f in deploy/k8s/agents/cronjob-*.yaml; do
    sed 's|moto-news-agents:latest|localhost:32000/moto-news-agents:latest|g' \
        "$f" | microk8s kubectl apply -f -
done
log_ok "Agents deployed"

# ===== Step 8: Verify =====
log_info "Step 8: Verifying deployment..."

echo ""
echo "=== K8s Resources ==="
microk8s kubectl -n moto-news get all
echo ""
echo "=== PVCs ==="
microk8s kubectl -n moto-news get pvc
echo ""

# Health check
log_info "Checking health..."
sleep 5
if curl -s http://localhost:30080/health | grep -q '"status":"ok"'; then
    log_ok "Aggregator API is healthy!"
else
    log_warn "Health check failed (pod may still be starting)"
fi

# ===== Done =====
echo ""
echo "=============================================="
echo -e "${GREEN}  Installation complete!${NC}"
echo "=============================================="
echo ""
echo "  Project:     $PROJECT_DIR"
echo "  API:         http://$(hostname -I | awk '{print $1}'):30080"
echo "  Health:      curl http://localhost:30080/health"
echo "  Stats:       curl http://localhost:30080/api/stats"
echo "  Full run:    curl -X POST http://localhost:30080/api/run"
echo "  Ollama:      http://localhost:11434"
echo ""
echo "  Next steps:"
echo "    1. Create GitHub token secret (for AI agents):"
echo "       microk8s kubectl -n moto-news create secret generic github-token \\"
echo "         --from-literal=GITHUB_TOKEN=ghp_your_token"
echo ""
echo "    2. Pull more models (optional):"
echo "       ollama pull deepseek-r1:8b"
echo "       ollama pull translategemma:12b"
echo ""
echo "    3. Trigger first pipeline:"
echo "       curl -X POST http://localhost:30080/api/fetch"
echo ""
