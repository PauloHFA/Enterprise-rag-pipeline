#!/bin/bash
# ============================================
# ENTERPRISE RAG - FREE TIER BOOTSTRAP SCRIPT
# Para VM Ubuntu 22.04/24.04 (Oracle Always Free, AWS, GCP, Azure, etc.)
# ============================================

set -euo pipefail

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ----------------------------------------
# CONFIGURAÇÕES
# ----------------------------------------
REPO_URL="https://github.com/SEU_USUARIO/enterprise-rag.git"  # ALTERE AQUI
PROJECT_DIR="/opt/enterprise-rag"
ENV_FILE="${PROJECT_DIR}/.env"

# ----------------------------------------
# FUNÇÕES
# ----------------------------------------
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "Execute como root: sudo $0"
        exit 1
    fi
}

install_docker() {
    log_info "Instalando Docker..."
    if command -v docker &> /dev/null; then
        log_success "Docker já instalado: $(docker --version)"
        return
    fi
    
    apt-get update
    apt-get install -y ca-certificates curl gnupg lsb-release
    
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    
    log_success "Docker instalado: $(docker --version)"
}

install_docker_compose() {
    log_info "Verificando Docker Compose..."
    if docker compose version &> /dev/null; then
        log_success "Docker Compose plugin disponível: $(docker compose version)"
        return
    fi
    
    log_warn "Docker Compose plugin não encontrado, instalando standalone..."
    curl -SL https://github.com/docker/compose/releases/download/v2.24.5/docker-compose-linux-x86_64 -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    log_success "Docker Compose instalado: $(docker-compose --version)"
}

clone_repo() {
    log_info "Clonando repositório..."
    if [[ -d "$PROJECT_DIR" ]]; then
        log_warn "Diretório $PROJECT_DIR já existe, atualizando..."
        cd "$PROJECT_DIR"
        git pull
    else
        git clone "$REPO_URL" "$PROJECT_DIR"
        cd "$PROJECT_DIR"
    fi
    log_success "Repositório clonado/atualizado em $PROJECT_DIR"
}

setup_env() {
    log_info "Configurando variáveis de ambiente..."
    
    if [[ ! -f "$ENV_FILE" ]]; then
        cp "${PROJECT_DIR}/.env.free" "$ENV_FILE"
        log_warn "Arquivo .env criado a partir do template .env.free"
        log_warn "IMPORTANTE: Edite $ENV_FILE e preencha todos os valores CHANGE_ME"
    else
        log_info "Arquivo .env já existe"
    fi
    
    # Gerar senhas aleatórias se não foram definidas
    if grep -q "CHANGE_ME" "$ENV_FILE"; then
        log_warn "Gerando senhas aleatórias para valores CHANGE_ME..."
        
        # PostgreSQL
        sed -i "s/CHANGE_ME_STRONG_POSTGRES_PASSWORD/$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)/" "$ENV_FILE"
        
        # RabbitMQ
        sed -i "s/CHANGE_ME_STRONG_RABBITMQ_PASSWORD/$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)/" "$ENV_FILE"
        
        # MinIO
        sed -i "s/CHANGE_ME_STRONG_MINIO_PASSWORD/$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)/" "$ENV_FILE"
        
        # API Key
        sed -i "s/CHANGE_ME_GENERATE_WITH_OPENSSL_RAND_HEX_32/$(openssl rand -hex 32)/" "$ENV_FILE"
        
        # Grafana
        sed -i "s/CHANGE_ME_GRAFANA_PASSWORD/$(openssl rand -base64 16 | tr -d '/+=' | cut -c1-16)/" "$ENV_FILE"
        
        log_success "Senhas geradas automaticamente no .env"
    fi
}

setup_ssl() {
    log_info "Configurando SSL..."
    
    mkdir -p "${PROJECT_DIR}/certs"
    
    if [[ ! -f "${PROJECT_DIR}/certs/fullchain.pem" ]] || [[ ! -f "${PROJECT_DIR}/certs/privkey.pem" ]]; then
        log_warn "Certificados SSL não encontrados."
        log_info "Opções:"
        echo "  1. Cloudflare Tunnel (recomendado, grátis, sem abrir portas):"
        echo "     - Instale cloudflared e configure tunnel para localhost:8000 e localhost:3000"
        echo "     - Não precisa de certificados locais"
        echo ""
        echo "  2. Let's Encrypt (precisa domínio apontando para esta VM):"
        echo "     - Instale certbot: apt-get install certbot"
        echo "     - Rode: certbot certonly --standalone -d seu-dominio.com"
        echo "     - Copie certificados para ${PROJECT_DIR}/certs/"
        echo ""
        echo "  3. Self-signed (apenas para teste):"
        echo "     - openssl req -x509 -nodes -days 365 -newkey rsa:2048 \\"
        echo "       -keyout ${PROJECT_DIR}/certs/privkey.pem \\"
        echo "       -out ${PROJECT_DIR}/certs/fullchain.pem \\"
        echo "       -subj '/CN=localhost'"
        
        read -p "Deseja gerar certificado self-signed para teste? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
                -keyout "${PROJECT_DIR}/certs/privkey.pem" \
                -out "${PROJECT_DIR}/certs/fullchain.pem" \
                -subj '/CN=localhost'
            log_success "Certificado self-signed gerado"
        fi
    else
        log_success "Certificados SSL já existem"
    fi
}

create_directories() {
    log_info "Criando diretórios de dados..."
    mkdir -p /opt/rag-data/{postgres,rabbitmq,minio,prometheus,grafana}
    chown -R 999:999 /opt/rag-data/postgres 2>/dev/null || true  # postgres user
    chown -R 1000:1000 /opt/rag-data/grafana 2>/dev/null || true  # grafana user
    log_success "Diretórios criados"
}

deploy_stack() {
    log_info "Fazendo deploy da stack..."
    cd "$PROJECT_DIR"
    
    # Pull images first
    log_info "Baixando imagens Docker..."
    docker compose -f docker-compose.free.yml pull
    
    # Build custom images
    log_info "Construindo imagens customizadas..."
    docker compose -f docker-compose.free.yml build --no-cache
    
    # Start services
    log_info "Iniciando serviços..."
    docker compose -f docker-compose.free.yml up -d
    
    log_success "Stack deployada!"
}

wait_for_services() {
    log_info "Aguardando serviços ficarem saudáveis..."
    
    local max_attempts=60
    local attempt=0
    
    while [[ $attempt -lt $max_attempts ]]; do
        if docker compose -f "$PROJECT_DIR/docker-compose.free.yml" ps --format json | grep -q '"Health":"healthy"'; then
            local healthy_count=$(docker compose -f "$PROJECT_DIR/docker-compose.free.yml" ps --format json | grep -c '"Health":"healthy"' || true)
            local total_count=$(docker compose -f "$PROJECT_DIR/docker-compose.free.yml" ps --format json | grep -c '"State":"running"' || true)
            
            if [[ $healthy_count -eq $total_count ]] && [[ $total_count -gt 0 ]]; then
                log_success "Todos os $total_count serviços estão saudáveis!"
                return 0
            fi
        fi
        
        attempt=$((attempt + 1))
        echo -n "."
        sleep 5
    done
    
    log_warn "Timeout aguardando serviços. Verifique com: docker compose -f docker-compose.free.yml ps"
}

show_status() {
    log_info "Status dos serviços:"
    cd "$PROJECT_DIR"
    docker compose -f docker-compose.free.yml ps
    
    echo ""
    log_info "URLs de acesso (ajuste conforme seu domínio/IP):"
    echo "  API:        https://SEU_DOMINIO_OU_IP/v1/docs"
    echo "  Grafana:    https://SEU_DOMINIO_OU_IP/grafana (user: admin, senha no .env)"
    echo "  MinIO:      https://SEU_DOMINIO_OU_IP:9001 (user: minioadmin, senha no .env)"
    echo "  RabbitMQ:   https://SEU_DOMINIO_OU_IP:15672 (user: raguser, senha no .env)"
    echo "  Prometheus: https://SEU_DOMINIO_OU_IP:9090"
    
    echo ""
    log_info "Comandos úteis:"
    echo "  Ver logs:     docker compose -f docker-compose.free.yml logs -f [serviço]"
    echo "  Reiniciar:    docker compose -f docker-compose.free.yml restart [serviço]"
    echo "  Parar:        docker compose -f docker-compose.free.yml down"
    echo "  Atualizar:    cd $PROJECT_DIR && git pull && docker compose -f docker-compose.free.yml build --no-cache && docker compose -f docker-compose.free.yml up -d"
    echo "  Backup:       $PROJECT_DIR/scripts/backup.sh /opt/rag-backups"
}

# ----------------------------------------
# MAIN
# ----------------------------------------
main() {
    echo "============================================"
    echo "  ENTERPRISE RAG - FREE TIER BOOTSTRAP"
    echo "============================================"
    echo ""
    
    check_root
    install_docker
    install_docker_compose
    clone_repo
    setup_env
    setup_ssl
    create_directories
    deploy_stack
    wait_for_services
    show_status
    
    echo ""
    log_success "Bootstrap concluído! 🎉"
    log_warn "Lembre-se de:"
    echo "  1. Configurar DNS para apontar para esta VM"
    echo "  2. Configurar Cloudflare Tunnel ou abrir portas 80/443 no firewall"
    echo "  3. Testar upload de documento: curl -X POST -H \"X-API-Key: \$(grep API_KEY .env | cut -d= -f2)\" -F \"file=@test.pdf\" https://SEU_DOMINIO/v1/documents"
}

main "$@"