# Plano de correção e implementação

Revisão: 14/10/2026. Referência: PDF Documentacao_Plano_de_Acao_Enterprise_RAG,
RF-01 a RF-10 e RNF-01 a RNF-08. Este plano substitui os estados de conclusão
desatualizados do IMPLEMENTATION_PLAN.md; existência de código não comprova aceite.

## 1. Estabilizar o fluxo mínimo — concluída e validada localmente

- Corrigir imports/entrypoints dos containers API e worker.
- Usar stream no upload MinIO; retirar acessos de rede e download do modelo dos imports.
- Unificar configuração da API; permitir variáveis adicionais no arquivo .env.
- Corrigir timestamps de criação, resposta de metadados e validação de JSON.
- Corrigir chunks extras no final do texto e validar tamanho/sobreposição.
- Fixar dependências compatíveis do modelo; carregar embeddings sob demanda.
- Atualizar sentence-transformers para 2.7.0: o loader anterior baixa também
  variantes ONNX/OpenVINO desnecessárias do repositório do modelo.
- Aguardar infraestrutura saudável e API pronta antes de iniciar o worker.
- Construir MinIO a partir de release upstream fixada; imagem antiga não disponível.
- Corrigir roteamento de retries e tornar mensagens publicadas persistentes.
- Corrigir testes unitários e adicionar integração real: upload, status, buscas
  lexical/vetorial/híbrida, deduplicação, reindexação e exclusão.
- CI da etapa 1 executa unitários isolados e pipeline Compose real. Gates de lint,
  tipagem, auditoria de dependências e cobertura mínima serão fechados na etapa 5;
  não são apresentados como aprovados enquanto não houver execução efetiva.

Aceite: testes unitários passam; em ambiente limpo, Docker Compose inicia;
arquivo TXT retorna 202 com UUID/metadados, chega a completed e aparece nos três
modos de busca. Reindexação preserva quantidade de chunks; exclusão elimina
documento, chunks e objeto. O teste real não substitui serviços nem embeddings por mocks.
Não encerrar esta etapa como validada enquanto o teste com serviços não passar.

## 2. Garantir confiabilidade — concluída e validada localmente

Guia técnico e runbook: [RELIABLE_DELIVERY.md](RELIABLE_DELIVERY.md).

- Outbox transacional e publicador recuperável; eliminar janela entre commit e evento.
- Publisher confirms no retry; TTL/backoff em filas próprias, reconexão e ferramenta de replay DLQ.
- Idempotência concorrente, bloqueio/versionamento de jobs e compensação de falhas no storage.
- Testes de mensagens repetidas e quedas de worker/broker; proteção contra corrida com exclusão.

Aceite: nenhum job aceito se perde; falhas transitórias se recuperam; mensagens
inválidas chegam à DLQ após limite documentado; reentrega não duplica chunks.

## 3. Completar recuperação documental — pendente

- Extração estruturada por página/seção, título e offsets; Markdown explícito e OCR opcional.
- Chunking semântico/tokenizado, configuração de tamanho/overlap e versão da estratégia.
- Embeddings em lote com dimensão validada; estados extracting/chunking/embedding/indexed.
- Filtros e parâmetros RRF tipados; citações com documento/página/trecho.

Aceite: corpus PDF/DOCX/HTML/TXT/MD preserva origem, citações e ordem; lotes
medidos; parâmetros inválidos retornam erro de validação.

## 4. Segurança e operação — pendente

- Limites de upload, detecção do tipo real e extratores sem privilégios.
- Alembic com migrações versionadas; esquema compartilhado API/worker.
- Instrumentar métricas nos fluxos, endpoint do worker, dashboards provisionados e alertas.
- Logs estruturados e tracing correlacionado; JWT/OIDC, testes de isolamento e retenção.
- Revisar Compose de produção/free, certificados, portas expostas e escala de réplicas.
- Backup que falha quando incompleto; exercício de restore em ambiente isolado.

Aceite: migração e restore comprovados; métricas variam após operações reais;
alertas disparados em teste; uploads rejeitados e isolamento de tenants comprovado.

## 5. Medir e fechar os critérios de aceite — pendente

- Dataset de 50–200 consultas com gabarito; recall@k, MRR e nDCG@10 nos três modos.
- Benchmark de p95 upload <300 ms e busca <500 ms; baseline de docs/min e chunks/s.
- CI com integração real, cobertura >=80% nos módulos de domínio e gates efetivos.
- Runbook, ADRs, contratos de eventos, relatório de avaliação/benchmark e demonstração.

Aceite: RNFs medidos ou desvios justificados; relatórios versionados;
documentação reflete resultados verificáveis. UI final, geração LLM e Kubernetes
permanecem extensões pós-MVP conforme o escopo do PDF.

## Verificação da etapa 1

Python recomendado: 3.11 (mesmo dos containers).

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe -r api/requirements.txt -r worker/requirements.txt pytest pytest-cov pytest-asyncio
.venv/Scripts/python.exe -m pytest tests/test_api.py tests/test_worker.py --no-cov -q
docker compose up -d --build
$env:RAG_E2E_URL = 'http://localhost:8000'
# RAG_E2E_API_KEY deve ser a mesma chave configurada na API.
.venv/Scripts/python.exe -m pytest tests/test_integration.py --no-cov -v
```

O teste real exige URL/chave explícitas e usa um documento único, removido ao final.
Ele acessa também PostgreSQL e MinIO para verificar cascata e exclusão do objeto.
Os defaults são os serviços locais do Compose de desenvolvimento. Para outro
ambiente de teste, configure RAG_E2E_DATABASE_URL, RAG_E2E_MINIO_ENDPOINT,
RAG_E2E_MINIO_ACCESS_KEY e RAG_E2E_MINIO_SECRET_KEY.
Se houver PostgreSQL instalado no host, defina POSTGRES_PORT=55432 antes de
subir o Compose e use essa porta em RAG_E2E_DATABASE_URL. Não interrompa o
serviço existente para executar os testes.
Na ausência dessas variáveis ele é marcado como skipped, nunca como aprovado.
O primeiro acesso ao modelo pode baixar pesos e demorar; o timeout é configurável.

## Resultado da etapa 1 — 13/10/2026

- 38 testes unitários aprovados com Python 3.11.16 e sentence-transformers 2.7.0.
- Build das três imagens aprovado, incluindo MinIO compilado da release upstream.
- Compose iniciou PostgreSQL, RabbitMQ, MinIO, API e worker; readiness respondeu 200.
- Teste real aprovado (14,43 s com pesos já em cache): TXT com UTF-8 e metadados,
  upload 202/UUID, completed, três modos de busca, deduplicação, reindexação com
  mesma quantidade de chunks e exclusão confirmada no banco e no object storage.
- O primeiro processamento baixou o modelo real; não foram usados embeddings simulados.
- Detectado PostgreSQL nativo ocupando 5432 no Windows. Teste executado com
  POSTGRES_PORT=55432, sem parar ou alterar esse serviço existente.
- CI ajustado para unitários sem serviços e pipeline Compose real; execução remota
  no GitHub Actions ainda precisa ocorrer após envio das alterações ao repositório.
- git diff --check e compilação Python sem erros.

Limites: esta validação cobre o Compose de desenvolvimento e um TXT de teste;
não comprova benchmark, qualidade de recuperação, segurança de produção, caos
nem todos os formatos. A cobertura inicial medida foi 62% antes dos novos casos
de retry; a meta de 80% continua pendente. Os Compose prod/free ainda exigem
a revisão da etapa 4, inclusive a imagem de storage e healthchecks.

## Resultado da etapa 2 — 14/10/2026

- Outbox transacional para upload/reindexação, publicador separado com confirmações,
  locks SKIP LOCKED, tentativas persistidas, backoff e IDs estáveis de evento.
- Proteção por geração e lock por documento; chunks e sucesso confirmados juntos.
- Exclusão lógica atômica com cleanup persistente; compensação de rollback de upload
  e comando de reconciliação para resultados incertos entre storage e banco.
- Retry com TTL 1/5/30s, quatro tentativas totais, confirmações antes do ACK,
  reconexão do worker e replay limitado da DLQ.
- 49 testes unitários aprovados. Medição de cobertura unitária: 62%; a meta de
  80% permanece pendente. Os processos reais dos containers não entram nessa medição.
- 7 testes reais aprovados (pipeline e seis cenários de resiliência): broker fora
  com reinício da API, duas réplicas de worker/publicador, uploads/reindexações
  concorrentes e reentrega sem trocar IDs de chunks, exclusão de job pendente,
  DLQ/replay após restaurar o arquivo original, rollback de outbox com compensação
  e interrupção do worker durante commit. Triggers de falha removidos ao final.
- Após os ajustes finais de reconexão, pipeline e recuperação de outage revalidados.
- Compose de desenvolvimento executado; configurações prod/free receberam o
  publicador e ajustes de dependências/storage, mas não foram implantadas.
- CI preparado para esses testes; execução remota depende do próximo commit/push.

Limites: entrega é at-least-once; original excluído é removido eventualmente;
reconciliação de órfãos não é automática; migrações reversíveis, métricas/alertas,
segurança, benchmarks e validação completa de prod/free continuam nas etapas 4/5.

Próxima implementação: etapa 3, extração com páginas/títulos, chunking semântico,
embeddings em lote, estados por etapa e citações completas.
