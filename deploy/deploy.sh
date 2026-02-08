#!/bin/bash
# ===================================================
# Deploy moto-news stack to microk8s
# ===================================================
# Usage:
#   ./deploy.sh [all|ollama|aggregator|agents]
#
# Prerequisites:
#   - microk8s installed and running
#   - microk8s addons: dns, storage, registry
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
    docker build -t localhost:32000/moto-news-aggregator:latest -f Dockerfile .
    docker push localhost:32000/moto-news-aggregator:latest 2>/dev/null || \
        log_warn "Could not push to local registry. Ensure microk8s registry is enabled."
    log_ok "Aggregator image built"

    # Build agents image
    log_info "Building agents image..."
    cd "$PROJECT_ROOT/agents"
    docker build -t localhost:32000/moto-news-agents:latest -f Dockerfile .
    docker push localhost:32000/moto-news-agents:latest 2>/dev/null || \
        log_warn "Could not push to local registry."
    log_ok "Agents image built"

    cd "$PROJECT_ROOT"
}

# ===== Deploy namespace =====
deploy_namespace() {
    log_info "Creating namespace..."
    microk8s kubectl apply -f "$K8S_DIR/base/namespace.yaml"
    log_ok "Namespace created"
}

# ===== Deploy Ollama =====
deploy_ollama() {
    log_info "Deploying Ollama..."
    microk8s kubectl apply -f "$K8S_DIR/ollama/service.yaml"
    microk8s kubectl apply -f "$K8S_DIR/ollama/statefulset.yaml"

    log_info "Waiting for Ollama to be ready..."
    microk8s kubectl -n moto-news rollout status statefulset/ollama --timeout=300s

    log_info "Pulling initial models (this will take a while on first run)..."
    microk8s kubectl apply -f "$K8S_DIR/ollama/init-models.yaml" 2>/dev/null || true

    log_ok "Ollama deployed"
}

# ===== Deploy Aggregator =====
deploy_aggregator() {
    log_info "Deploying Aggregator..."

    # Update image reference to local registry
    microk8s kubectl apply -f "$K8S_DIR/aggregator/configmap.yaml"
    microk8s kubectl apply -f "$K8S_DIR/aggregator/pvc.yaml"
    microk8s kubectl apply -f "$K8S_DIR/aggregator/service.yaml"

    # Patch deployment to use local registry image
    sed 's|moto-news-aggregator:latest|localhost:32000/moto-news-aggregator:latest|g' \
        "$K8S_DIR/aggregator/deployment.yaml" | microk8s kubectl apply -f -

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

    # Patch CronJobs to use local registry
    for f in "$K8S_DIR/agents/cronjob-"*.yaml; do
        sed 's|moto-news-agents:latest|localhost:32000/moto-news-agents:latest|g' \
            "$f" | microk8s kubectl apply -f -
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
    log_info "=== Access ==="
    echo "  Aggregator API: http://<server-ip>:30080"
    echo "  Health check:   curl http://localhost:30080/health"
    echo ""
}

# ===== Main =====
COMPONENT="${1:-all}"

check_prerequisites

case "$COMPONENT" in
    all)
        build_images
        deploy_namespace
        deploy_ollama
        deploy_aggregator
        deploy_agents
        show_status
        ;;
    ollama)
        deploy_namespace
        deploy_ollama
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
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 [all|ollama|aggregator|agents|status]"
        exit 1
        ;;
esac

log_ok "Deployment complete!"
