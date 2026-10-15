# Decisões de arquitetura

## ADR-001 — Outbox e entrega at-least-once

Documento/geração e evento precisam sobreviver juntos. A API grava outbox na
transação PostgreSQL e um publicador separado usa confirms, SKIP LOCKED e backoff.
ACK só segue commit do índice. Locks por documento e indexed_generation protegem
contra duplicatas/corridas. A consequência é entrega repetível e cleanup eventual,
com ferramentas de inspeção/replay/reconciliação; não há promessa exactly-once.

## ADR-002 — Modelo compartilhado e migração versionada

API e worker reexportam os mesmos modelos SQLAlchemy 2 tipados. Alembic adota
o esquema legado conhecido e serializa upgrades sob advisory lock. Dimensão 384
é um contrato do banco; mudar dimensão requer migração/reindexação. Downgrade
destrutivo requer escritores parados e backup. A API ainda migra no startup;
separar identidades DDL/runtime exige pré-migração na operação escolhida.

## ADR-003 — Proveniência e janela real do modelo

Offsets são caracteres do texto normalizado, por página/seção, sem paginação
física inventada para DOCX. Chunking usa o tokenizer e a janela real de 128 tokens,
incluindo contexto de título/seção. Isso é menor que a sugestão genérica 300–500
tokens do plano inicial porque o modelo escolhido truncaria esses chunks.
Embeddings são em lotes; extração roda em subprocesso com recursos limitados.

## ADR-004 — Relevância medida sem alegar homologação empresarial

O conjunto versionado tem 20 políticas sintéticas e 60 consultas únicas em
português, com julgamentos graduados (3 principal, 1 relacionado). Não contém
dados pessoais nem documentos da empresa. A avaliação usa os primeiros dez
chunks da API, deduplica documentos na ordem retornada e calcula recall@1/5/10,
MRR@10 e nDCG@10 com ganho 2^grau−1. Duplicatas não somam ganho.

O limite de regressão foi definido antes da medição: híbrido recall@10 >=0,90,
MRR@10 >=0,70 e nDCG@10 >=0,75. É um alarme para este fixture, não um SLA de
relevância real. A escolha de pesos/modelo não foi ajustada para vencer esse
gabarito após o resultado. Perguntas reais, juízes humanos e um holdout separado
são necessários para calibrar produção e evitar ajuste ao conjunto de regressão.

## ADR-005 — Desempenho e cobertura medidos em execuções distintas

Coverage altera especialmente loops de validação de texto. Por isso as imagens
instrumentadas só são usadas para cobertura/integração, com shards por processo.
Métricas de latência usam o target production sem coverage, warmups explícitos,
erros e amostras brutas; primeira carga é registrada separadamente. O override
benchmark desliga COVERAGE_PROCESS_START e o relatório registra sua ausência.

A busca roda em thread do FastAPI, mantendo trabalho síncrono fora do event loop.
Carregamento do modelo tem lock para não duplicar pesos no cold start; encode
usa lock porque tokenizer/modelo são compartilhados por threads. Esse limite
é uma escolha de segurança/CPU para o cenário atual; batching entre requisições
e escalabilidade devem ser medidos antes de mudanças de concorrência.

## ADR-006 — Auditoria sem ignorar wheels CPU

pip-audit pode ignorar torch+cpu por não encontrar essa versão local no PyPI.
O script audita o snapshot completo instalado, normalizando +cpu para a versão
upstream, com --strict e falha se houver pacote ignorado. Isso verifica advisories
do PyTorch correspondente; não é análise binária do wheel nem scanner do SO.
As versões vulneráveis foram atualizadas e o modelo teve revisão fixada.
Reexecute o gate em toda alteração e periodicamente: o feed muda com novos
advisories. A auditoria não usa uma lista geral de exceções para passar.

Referência da migração de embeddings: [Sentence Transformers migration guide](https://sbert.net/docs/migration_guide.html).

## ADR-007 — Credenciais locais e domínio sob controle do operador

O gerador usa secrets.token_urlsafe, cria .env.production exclusivamente e não
altera senhas existentes. Arquivos privados e certificados são ignorados pelo
Git; exemplos não contêm segredos reais. Produção usa projeto Compose separado
para não reutilizar volumes com credenciais de desenvolvimento. A aplicação não
requer API paga de LLM: é ingestão/busca com embeddings CPU. DNS, certificado
confiável, provedor OIDC opcional e notificações externas dependem do ambiente
do operador; nenhum desses serviços foi criado automaticamente.
