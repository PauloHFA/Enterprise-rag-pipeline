# Entrega confiável de documentos

Implementação da etapa 2, iniciada em 14/10/2026. O transporte é **at-least-once**:
mensagens podem repetir; a indexação de cada geração é idempotente.

## Upload e outbox

O upload grava `documents` e `outbox_events` na mesma transação PostgreSQL.
HTTP 202 significa que ambos foram confirmados no banco, independentemente do
broker. O arquivo original já está no MinIO. Uploads iguais no mesmo tenant são
serializados por hash e retornam o mesmo ID, sem criar outro evento.

O serviço `outbox` roda `python -m api.outbox`. Ele seleciona eventos pendentes
com `FOR UPDATE SKIP LOCKED`, publica mensagens persistentes com ID estável,
`mandatory` e publisher confirms, e só então confirma `published_at` no banco.
É possível escalar os publicadores. Falhas persistem `attempts`, `last_error`
(classe do erro) e `next_attempt_at`, com backoff exponencial limitado a 60s.
Eventos de upload não têm limite de tentativas de publicação: permanecem
recuperáveis enquanto o documento existir.

Se o processo morrer depois do confirm e antes do commit, ele publica novamente.
Cada documento tem `generation` e `indexed_generation`. O worker descarta eventos
de gerações antigas e gerações já indexadas. Reindexar incrementa a geração e
grava um novo evento na mesma transação. Chunks e marcador de sucesso são
confirmados juntos; uma falha de ACK não refaz uma indexação concluída.

## Concorrência e exclusão

Worker, reindexação e exclusão compartilham um advisory lock por documento.
O worker mantém o lock até terminar/rollback e o libera antes de devolver a
conexão ao pool. Réplicas não indexam o mesmo documento simultaneamente.

DELETE remove documento, chunks e eventos associados no banco e grava um evento
`storage.delete` independente na mesma transação. O arquivo no MinIO é removido
**eventualmente** pelo publicador, com retries persistentes, inclusive quando o
RabbitMQ está fora. HTTP 204 confirma a exclusão lógica; o objeto pode permanecer
até esse cleanup terminar. Uma mensagem antiga de documento excluído é ACK sem
baixar o arquivo nem recriar chunks.

Se o commit de upload falhar, a API verifica seu resultado antes de compensar o
arquivo. Um commit de resultado incerto nunca provoca exclusão cega de um arquivo
possivelmente válido. Se o banco confirmar rollback, remove-se o original; se o
MinIO falhar, grava-se cleanup recuperável. Se o banco também estiver inacessível,
a API retorna 503 e registra a chave para reconciliação operacional posterior.

## Retries do worker e DLQ

Há três filas de retry por tipo de evento, com TTL padrão de **1s, 5s e 30s**.
Elas devolvem a mensagem à exchange original após o atraso. O worker incrementa
`x-retry-count`, mantém `message_id` e só ACKa a mensagem anterior depois de
confirmar a publicação do retry. Falha de publicação reentrega o original.
Depois de três retries (quatro tentativas), a mensagem vai para a DLQ.
O worker reconecta automaticamente ao broker com atraso de até 30s.

`MESSAGE_RETRY_DELAYS_MS` deve ser igual em worker e publicador. As propriedades
TTL de filas existentes são imutáveis no RabbitMQ: mudar esses valores exige
planejar substituição das filas de retry, preservando mensagens em trânsito.
Não remova filas ou volumes para resolver um conflito de configuração.

Inspeção da DLQ (não reenvia por padrão):

```bash
docker compose exec worker python -m worker.replay_dlq --queue document.upload.dlq
```

Após corrigir a causa do erro, reenvio limitado:

```bash
docker compose exec worker python -m worker.replay_dlq --queue document.upload.dlq --limit 10 --execute
```

O replay confirma a republicação antes do ACK na DLQ, preserva o ID do evento e
zera o contador de retry. Eventos antigos/excluídos continuam sendo descartados.
Inspecione também `document.reindex.dlq` quando houver falhas de reindexação.

## Reconciliação de storage

Para falhas de resultado incerto entre storage e banco, inspecione objetos antigos
sem documento nem cleanup pendente. O banco deve estar disponível. Por padrão,
objetos de menos de 24h são preservados para proteger uploads em andamento.

```bash
docker compose exec outbox python -m api.reconcile_storage --min-age-hours 24
docker compose exec outbox python -m api.reconcile_storage --min-age-hours 24 --limit 100 --execute
```

`--execute` agenda cleanup na outbox; não apaga diretamente. A revisão de órfãos
deve ocorrer depois da recuperação de falhas de banco/storage. Essa rotina não
está agendada automaticamente: a política operacional/retencão pertence à etapa 4.

## Esquema e disponibilidade

O startup aplica upgrade aditivo para bancos existentes: cria a outbox e adiciona
generation/indexed_generation com defaults. Documentos já `completed` são
marcados como indexados. Um lock serializa inicializações concorrentes. Os Compose
iniciam publicador e workers após a API pronta. Alembic e migrações reversíveis
continuam planejados na etapa 4; faça backup antes de promover para produção.
Atualize API, publicador e workers juntos; não mantenha workers antigos processando
eventos da versão nova, pois eles não aplicam a proteção por geração.

`/readyz` verifica PostgreSQL, sem exigir RabbitMQ conectado. Uma API saudável
pode ter jobs esperando o broker; acompanhe o atraso da outbox e as DLQs.
As métricas e alertas automáticos correspondentes serão instrumentados na etapa 4.

## Testes de falha

`tests/test_resilience.py` exige explicitamente `RAG_TEST_COMPOSE_PROJECT`, URL,
chave e conexão PostgreSQL de **um ambiente descartável**. O teste para/reinicia
serviços e instala triggers temporários para provocar rollback/interromper commit.
Execute somente nesse projeto isolado. As fixtures preservam serviços de outros
projetos e removem os triggers de teste. O CI usa `enterprise-rag-ci`.

```powershell
$env:RAG_TEST_COMPOSE_PROJECT = 'rag-flow-review'
$env:RAG_E2E_URL = 'http://localhost:8000'
# Configure RAG_E2E_API_KEY e RAG_E2E_DATABASE_URL para esse stack.
.venv/Scripts/python.exe -m pytest tests/test_resilience.py --no-cov -v
```

Os testes verificam indisponibilidade do broker, reinício da API, publicadores e
workers concorrentes, reentrega, reindexação, exclusão de job pendente, rollback
de outbox, morte do worker durante commit, TTL/backoff, DLQ e replay após reparo.
