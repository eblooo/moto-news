#!/bin/bash
# ===================================================
# Deploy moto-news stack to microk8s
# ===================================================
# Usage:
#   ./deploy.sh [all|aggregator|agents|models|status]
#
# Ollama runs on the HOST (not in K8s).
# K8s pods access it via host.cni.cncf.io:11434
#
# Prerequisites:
#   - microk8s installed and running
#   - microk8s addons: dns, storage, registry
#   - Ollama installed and running on the host
# ===================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
K8S_DIR="$SCRIPT_DIR/k8s"

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

# ===== Check prerequisites =====
check_prerequisites() {
    log_info "Checking prerequisites..."

    if ! command -v microk8s &> /dev/null; then
        log_error "microk8s not found. Install it: sudo snap install microk8s --classic"
        exit 1
    fi

    # Check microk8s status
    if ! microk8s status --wait-ready &> /dev/null; then
        log_error "microk8s is not running. Start it: microk8s start"
        exit 1
    fi

    # Check Ollama on host
    if curl -s http://localhost:11434/ > /dev/null 2>&1; then
        log_ok "Ollama is running on host"
    else
        log_warn "Ollama not detected on localhost:11434"
        log_warn "Install: curl -fsSL https://ollama.com/install.sh | sh"
        log_warn "Start:   systemctl start ollama  (or: ollama serve)"
    fi

    # Ensure Ollama listens on all interfaces (for K8s pods)
    if systemctl is-active ollama > /dev/null 2>&1; then
        if ! grep -q "OLLAMA_HOST=0.0.0.0" /etc/systemd/system/ollama.service.d/override.conf 2>/dev/null; then
            log_warn "Ollama may only listen on localhost."
            log_warn "For K8s pods to reach it, run:"
            log_warn "  sudo mkdir -p /etc/systemd/system/ollama.service.d"
            log_warn "  echo -e '[Service]\nEnvironment=OLLAMA_HOST=0.0.0.0' | sudo tee /etc/systemd/system/ollama.service.d/override.conf"
            log_warn "  sudo systemctl daemon-reload && sudo systemctl restart ollama"
        fi
    fi

    # Enable required addons
    log_info "Ensuring required addons are enabled..."
    microk8s enable dns storage registry 2>/dev/null || true

    log_ok "Prerequisites OK"
}

# ===== Build Docker images =====
build_images() {
    log_info "Building Docker images..."

    # Build aggregator image
    log_info "Building aggregator image..."
    cd "$PROJECT_ROOT"
    docker build -t klimdos/moto-news-aggregator:latest -f Dockerfile .
    docker push klimdos/moto-news-aggregator:latest 2>/dev/null || \
        log_warn "Could not push to Docker Hub. Ensure you are logged in (docker login)."
    log_ok "Aggregator image built"

    # Build agents image
    log_info "Building agents image..."
    cd "$PROJECT_ROOT/agents"
    docker build -t klimdos/moto-news-agents:latest -f Dockerfile .
    docker push klimdos/moto-news-agents:latest 2>/dev/null || \
        log_warn "Could not push to Docker Hub."
    log_ok "Agents image built"

    cd "$PROJECT_ROOT"
}

# ===== Deploy namespace =====
deploy_namespace() {
    log_info "Creating namespace..."
    microk8s kubectl apply -f "$K8S_DIR/base/namespace.yaml"
    log_ok "Namespace created"
}

# ===== Pull models on host Ollama =====
pull_models() {
    log_info "Pulling models on host Ollama..."

    if ! curl -s http://localhost:11434/ > /dev/null 2>&1; then
        log_error "Ollama is not running on host. Start it first."
        return 1
    fi

    for model in "llama3.2:3b" "qwen2.5-coder:7b" "deepseek-r1:8b"; do
        log_info "Pulling $model ..."
        curl -s http://localhost:11434/api/pull -d "{\"name\": \"$model\"}" | \
            while IFS= read -r line; do
                status=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || true)
                if [ -n "$status" ]; then
                    printf "\r  %s: %s          " "$model" "$status"
                fi
            done
        echo ""
        log_ok "$model pulled"
    done

    log_info "Available models:"
    curl -s http://localhost:11434/api/tags | python3 -c "
import json,sys
d=json.load(sys.stdin)
for m in d['models']:
    print(f\"  {m['name']:30s} {m['details']['parameter_size']:>8s}  {m['details']['quantization_level']}\")
" 2>/dev/null || true
}

# ===== Deploy Aggregator =====
deploy_aggregator() {
    log_info "Deploying Aggregator..."

    microk8s kubectl apply -f "$K8S_DIR/aggregator/configmap.yaml"
    microk8s kubectl apply -f "$K8S_DIR/aggregator/pvc.yaml"
    microk8s kubectl apply -f "$K8S_DIR/aggregator/service.yaml"

    microk8s kubectl apply -f "$K8S_DIR/aggregator/deployment.yaml"

    microk8s kubectl apply -f "$K8S_DIR/aggregator/cronjob.yaml"

    log_info "Waiting for Aggregator to be ready..."
    microk8s kubectl -n moto-news rollout status deployment/aggregator --timeout=120s

    log_ok "Aggregator deployed"
}

# ===== Deploy Agents =====
deploy_agents() {
    log_info "Deploying AI Agents..."

    microk8s kubectl apply -f "$K8S_DIR/agents/configmap.yaml"

    # Check if secret exists
    if ! microk8s kubectl -n moto-news get secret github-token &> /dev/null; then
        log_warn "GitHub token secret not found."
        log_warn "Create it with:"
        log_warn "  microk8s kubectl -n moto-news create secret generic github-token \\"
        log_warn "    --from-literal=GITHUB_TOKEN=ghp_your_token_here"
        log_warn ""
        log_warn "Agents will run in dry-run mode without the token."
    fi

    for f in "$K8S_DIR/agents/cronjob-"*.yaml; do
        microk8s kubectl apply -f "$f"
    done

    log_ok "Agents deployed"
}

# ===== Status =====
show_status() {
    echo ""
    log_info "=== Deployment Status ==="
    echo ""
    microk8s kubectl -n moto-news get all
    echo ""
    microk8s kubectl -n moto-news get pvc
    echo ""

    log_info "=== Ollama (host) ==="
    if curl -s http://localhost:11434/ > /dev/null 2>&1; then
        echo "  Status: running"
        curl -s http://localhost:11434/api/tags | python3 -c "
import json,sys
d=json.load(sys.stdin)
for m in d['models']:
    print(f\"  Model: {m['name']} ({m['details']['parameter_size']})\")
" 2>/dev/null || true
    else
        echo "  Status: NOT RUNNING"
    fi
    echo ""

    log_info "=== Access ==="
    echo "  Aggregator API: http://<server-ip>:30080"
    echo "  Health check:   curl http://localhost:30080/health"
    echo "  Ollama:         http://localhost:11434"
    echo ""
}

# ===== Main =====
COMPONENT="${1:-all}"

check_prerequisites

case "$COMPONENT" in
    all)
        build_images
        deploy_namespace
        deploy_aggregator
        deploy_agents
        show_status
        ;;
    aggregator)
        build_images
        deploy_namespace
        deploy_aggregator
        ;;
    agents)
        build_images
        deploy_namespace
        deploy_agents
        ;;
    models)
        pull_models
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 [all|aggregator|agents|models|status]"
        exit 1
        ;;
esac

log_ok "Deployment complete!"
