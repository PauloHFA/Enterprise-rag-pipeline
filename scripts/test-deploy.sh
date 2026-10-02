#!/bin/bash
# ============================================
# ENTERPRISE RAG - DEPLOYMENT TEST SCRIPT
# Testa se o deploy free tier está funcionando
# ============================================

set -euo pipefail

# Configurações
BASE_URL="${BASE_URL:-https://localhost}"
API_KEY="${API_KEY}"

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[TEST]${NC} $1"; }
log_pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
log_fail() { echo -e "${RED}[FAIL]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# ----------------------------------------
# TESTES
# ----------------------------------------

test_health() {
    log_info "Testando /healthz..."
    local response=$(curl -s -k -w "%{http_code}" -o /dev/null "$BASE_URL/healthz" || echo "000")
    if [[ "$response" == "200" ]]; then
        log_pass "/healthz OK"
        return 0
    else
        log_fail "/healthz falhou (HTTP $response)"
        return 1
    fi
}

test_ready() {
    log_info "Testando /readyz..."
    local response=$(curl -s -k -w "%{http_code}" -o /dev/null "$BASE_URL/readyz" || echo "000")
    if [[ "$response" == "200" ]]; then
        log_pass "/readyz OK"
        return 0
    else
        log_fail "/readyz falhou (HTTP $response)"
        return 1
    fi
}

test_metrics() {
    log_info "Testando /metrics..."
    local response=$(curl -s -k -w "%{http_code}" -o /dev/null "$BASE_URL/metrics" || echo "000")
    if [[ "$response" == "200" ]]; then
        log_pass "/metrics OK"
        return 0
    else
        log_fail "/metrics falhou (HTTP $response)"
        return 1
    fi
}

test_auth_required() {
    log_info "Testando autenticação obrigatória..."
    local response=$(curl -s -k -w "%{http_code}" -o /dev/null -X POST "$BASE_URL/v1/documents" -F "file=@/dev/null" || echo "000")
    if [[ "$response" == "401" ]]; then
        log_pass "Autenticação obrigatória funcionando (401 sem API key)"
        return 0
    else
        log_fail "Autenticação não está bloqueando (HTTP $response)"
        return 1
    fi
}

test_upload_document() {
    log_info "Testando upload de documento..."
    
    # Criar arquivo de teste
    local test_file="/tmp/test-doc.txt"
    echo "Este é um documento de teste para o Enterprise RAG.
Contém múltiplas linhas de texto.
Serve para validar o pipeline de ingestão e busca.
Palavras-chave: enterprise, rag, pipeline, teste, validação." > "$test_file"
    
    local response=$(curl -s -k -w "\n%{http_code}" -X POST "$BASE_URL/v1/documents" \
        -H "X-API-Key: $API_KEY" \
        -F "file=@$test_file" \
        -F "tenant_id=test-tenant" \
        -F "metadata={\"category\":\"test\",\"source\":\"deploy-test\"}" || echo -e "\n000")
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n -1)
    
    if [[ "$http_code" == "202" ]]; then
        local doc_id=$(echo "$body" | grep -o '"id":"[^"]*"' | cut -d'"' -f4)
        log_pass "Upload OK (202) - Document ID: $doc_id"
        echo "$doc_id" > /tmp/last_doc_id.txt
        return 0
    else
        log_fail "Upload falhou (HTTP $http_code): $body"
        return 1
    fi
}

test_document_status() {
    log_info "Testando consulta de status do documento..."
    
    local doc_id=$(cat /tmp/last_doc_id.txt 2>/dev/null || echo "")
    if [[ -z "$doc_id" ]]; then
        log_warn "Sem document ID, pulando teste de status"
        return 0
    fi
    
    local response=$(curl -s -k -w "\n%{http_code}" -X GET "$BASE_URL/v1/documents/$doc_id" \
        -H "X-API-Key: $API_KEY" || echo -e "\n000")
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n -1)
    
    if [[ "$http_code" == "200" ]]; then
        local status=$(echo "$body" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
        log_pass "Status OK (200) - Status: $status"
        return 0
    else
        log_fail "Status falhou (HTTP $http_code): $body"
        return 1
    fi
}

test_search() {
    log_info "Testando busca híbrida..."
    
    # Aguardar processamento
    log_info "Aguardando 10s para processamento do worker..."
    sleep 10
    
    local response=$(curl -s -k -w "\n%{http_code}" -X POST "$BASE_URL/v1/search" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $API_KEY" \
        -d '{"query": "enterprise rag pipeline", "top_k": 5, "mode": "hybrid"}' || echo -e "\n000")
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n -1)
    
    if [[ "$http_code" == "200" ]]; then
        local results_count=$(echo "$body" | grep -o '"chunk_id"' | wc -l)
        log_pass "Busca OK (200) - Resultados: $results_count"
        if [[ $results_count -gt 0 ]]; then
            log_pass "Busca retornou resultados relevantes!"
        else
            log_warn "Busca não retornou resultados (documento pode não ter sido processado ainda)"
        fi
        return 0
    else
        log_fail "Busca falhou (HTTP $http_code): $body"
        return 1
    fi
}

test_rate_limit() {
    log_info "Testando rate limiting (fazendo 65 requisições rápidas)..."
    
    local blocked=0
    for i in {1..65}; do
        local response=$(curl -s -k -w "%{http_code}" -o /dev/null -X GET "$BASE_URL/healthz" || echo "000")
        if [[ "$response" == "429" ]]; then
            blocked=1
            break
        fi
    done
    
    if [[ $blocked -eq 1 ]]; then
        log_pass "Rate limiting funcionando (429 após limite)"
        return 0
    else
        log_warn "Rate limiting não bloqueou (pode estar desabilitado ou limite alto)"
        return 0  # Não é falha crítica
    fi
}

test_grafana() {
    log_info "Testando Grafana..."
    local response=$(curl -s -k -w "%{http_code}" -o /dev/null "$BASE_URL/grafana/api/health" || echo "000")
    if [[ "$response" == "200" ]]; then
        log_pass "Grafana OK"
        return 0
    else
        log_fail "Grafana falhou (HTTP $response)"
        return 1
    fi
}

test_prometheus() {
    log_info "Testando Prometheus..."
    local response=$(curl -s -k -w "%{http_code}" -o /dev/null "$BASE_URL:9090/-/healthy" || echo "000")
    if [[ "$response" == "200" ]]; then
        log_pass "Prometheus OK"
        return 0
    else
        log_fail "Prometheus falhou (HTTP $response)"
        return 1
    fi
}

# ----------------------------------------
# MAIN
# ----------------------------------------
main() {
    echo "============================================"
    echo "  ENTERPRISE RAG - DEPLOYMENT TESTS"
    echo "============================================"
    echo "Base URL: $BASE_URL"
    echo ""
    
    if [[ -z "$API_KEY" ]]; then
        log_error "API_KEY não definida. Use: export API_KEY=sua_chave"
        exit 1
    fi
    
    local passed=0
    local failed=0
    
    # Testes básicos
    test_health && ((passed++)) || ((failed++))
    test_ready && ((passed++)) || ((failed++))
    test_metrics && ((passed++)) || ((failed++))
    test_auth_required && ((passed++)) || ((failed++))
    
    # Testes funcionais
    test_upload_document && ((passed++)) || ((failed++))
    test_document_status && ((passed++)) || ((failed++))
    test_search && ((passed++)) || ((failed++))
    
    # Testes de infra
    test_rate_limit && ((passed++)) || ((failed++))
    test_grafana && ((passed++)) || ((failed++))
    test_prometheus && ((passed++)) || ((failed++))
    
    echo ""
    echo "============================================"
    echo "  RESULTADO: $passed passed, $failed failed"
    echo "============================================"
    
    if [[ $failed -eq 0 ]]; then
        log_success "Todos os testes passaram! 🎉"
        exit 0
    else
        log_error "$failed teste(s) falharam"
        exit 1
    fi
}

main "$@"