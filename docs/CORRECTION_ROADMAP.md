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

## 3. Completar recuperação documental — concluída e validada localmente

Guia técnico: [DOCUMENT_PROCESSING.md](DOCUMENT_PROCESSING.md).

- Extração estruturada por página/seção, título e offsets; Markdown explícito e OCR opcional.
- Chunking semântico/tokenizado, configuração de tamanho/overlap e versão da estratégia.
- Embeddings em lote com dimensão validada; estados extracting/chunking/embedding/indexed.
- Filtros e parâmetros RRF tipados; citações com documento/página/trecho.

Aceite: corpus PDF/DOCX/HTML/TXT/MD preserva origem, citações e ordem; lotes
medidos; parâmetros inválidos retornam erro de validação.

## 4. Segurança e operação — concluída e validada localmente

Guia técnico e runbook: [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md).

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

## Resultado da etapa 3 — 14/10/2026

- Extração por página/seção nos cinco formatos, com título, headings, tabelas e
  offsets sobre texto normalizado; OCR opcional para scans com/sem rodapé textual.
- Chunking limitado pelo tokenizer real e pela janela do modelo (128 tokens),
  com sobreposição e contexto de título/seção sem alterar o conteúdo citado.
- Embeddings em lote, validação de dimensão/finitude e medições por documento.
- Estados queued/extracting/chunking/embedding/indexed/failed e histórico por geração.
- Citações estruturadas nos três modos; filtros por tags/datas e RRF tipados.
- 73 testes unitários aprovados; cobertura unitária global 68,1% (extração 96%,
  chunking 91%). A meta global de 80% continua pendente.
- 16 cenários reais aprovados: pipeline, seis testes de falha/concorrência da
  etapa 2 e nove casos de corpus, incluindo hierarquia com níveis pulados e OCR.
- No ensaio local, 100 chunks foram gerados em 50 lotes de 2, em cerca de 1,69s
  na geração de embeddings. Isso exclui extração/OCR e carregamento do modelo;
  não é benchmark de produção nem comprovação das metas p95.
- Relatório completo: `.test-results/document-corpus.json`; CI publica o artifact.
- Esquema aditivo e conversão do status legado completed para indexed verificados
  no banco de desenvolvimento existente. Dados de teste e triggers temporários removidos.

Limites: DOCX não tem paginação física inferida; offsets são do texto normalizado;
OCR não garante reconhecimento de layouts complexos; títulos/posições de chunks
antigos exigem reindexação; migrações reversíveis e deploy de prod/free continuam
na etapa 4. Execução remota do CI aguarda o próximo commit/push.

## Resultado da etapa 4 — 14/10/2026

- Upload limitado antes do parser, inclusive sem Content-Length; conferência de
  extensão/MIME/conteúdo e limite de expansão DOCX. Extração em subprocesso com
  timeout/limites de recursos; runtime UID 10001, sem capabilities e filesystem
  somente leitura. Nomes não escolhem diretórios de storage.
- API/worker compartilham shared/models.py. Alembic versiona baseline e evolução,
  serializa inicialização concorrente, adota esquema legado e normaliza título
  TEXT/VARCHAR. Upgrade novo, downgrade, adoção com dados e alembic check passaram.
  Template de nova revisão gerado e compilado no diretório temporário de testes.
- JWT RS256 com assinatura/issuer/audience/exp/claims obrigatórios, JWKS HTTPS e
  cache; API keys preservadas. Testes verificam tokens inválidos, configuração
  ambígua, indisponibilidade do provedor e isolamento de UUID/buscas/deduplicação.
- Métricas instrumentadas e coletadas de API, worker e publicador; dashboard
  provisionado no Grafana e regras validadas com promtool. Alerta de worker
  indisponível disparou e recuperou em teste. Logs JSON e traceparent foram
  correlacionados entre resposta HTTP, outbox e worker.
- Compose prod/free revisados: credenciais explícitas, backend sem host ports,
  monitoramento em loopback, TLS no Nginx e workers/publicadores escaláveis.
  Proxy HTTPS foi exercitado com certificado temporário confiado pelo cliente;
  métricas públicas foram bloqueadas. Configurações prod/free passaram no config.
- API com modelo carregado chegou a aproximadamente 1,04 GiB no ensaio local;
  limite de produção/free aumentado de 1 para 2 GiB. Isso não é benchmark.
- Backup completo com dump PostgreSQL major 15, objetos, manifesto/checksums e
  recusa de componentes ausentes. Restore comprovado em banco/bucket/vhost
  separados: contagens e hashes conferidos, três buscas respondendo e documento
  que estava na fila voltou a ser indexado via recuperação da outbox.
- Retenção remove somente documentos expirados, com lock e cleanup persistente;
  outbox pendente não é purgada. Arquivos .env locais são ignorados; o template
  de produção/free é .env.free.example. Os arquivos locais existentes foram preservados.
- 106 testes unitários aprovados; cobertura api/worker 64,2%, segurança 90% e
  uploads 89%. Processos reais em containers não entram nessa cobertura; a meta
  de 80% e a ampliação da instrumentação de cobertura seguem para a etapa 5.
- 23 cenários reais aprovados em rodadas locais: pipeline mínimo, nove casos de
  corpus/OCR, seis testes de falha/concorrência e sete testes operacionais.
  Relatórios locais em .test-results, com publicação de artifacts preparada no CI.
- git diff --check, compilação Python, configurações Compose e template Alembic
  verificados. Recursos do restore e certificados temporários removidos pelo teste.

Limites: implantação pública de prod/free não executada; requer credenciais,
domínio e certificados reais. OIDC foi validado com assinaturas reais em testes,
com transporte JWKS substituído no unitário; nenhum provedor externo foi conectado.
Tracing é correlação W3C/logs, sem exportador externo de spans. Alertas estão no
Prometheus, sem receiver externo configurado. Retenção exige agendamento explícito;
backup exige escritores parados e restore exige destino vazio. Windows aplica
timeout de extração; limites resource são do Linux. CI remoto aguarda commit/push.

Próxima implementação: etapa 5, avaliação de recuperação, benchmarks, cobertura
dos módulos de domínio, gates de CI e fechamento dos critérios de aceite.
