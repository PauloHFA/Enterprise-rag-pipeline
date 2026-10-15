# Contratos de eventos e operação

O contrato dos produtores atuais está em
[document-event.schema.json](../contracts/document-event.schema.json), versão
de documentação v1. O payload não possui event_type/schema_version: a rota define
upload/reindex. Não foi inventado um campo obrigatório incompatível com os eventos
existentes. Novos campos devem ser aditivos e consumidores devem tolerá-los.

| Operação | Exchange/rota | Fila |
| --- | --- | --- |
| Novo documento | document.upload | document.upload.queue |
| Reindexação | document.reindex | document.reindex.queue |
| Retry | exchange default; rota document.upload.retry.N ou document.reindex.retry.N | mesma rota, N=1/2/3 |
| Rejeição final | document.dlx; rota document.upload.dlq ou document.reindex.dlq | respectiva DLQ |

Campos: event_id é o UUID estável da outbox, também message_id no RabbitMQ;
document_id identifica o registro; tenant_id e generation devem corresponder
ao estado atual do banco. Storage, filename, MIME e metadata contextualizam o
evento, mas o worker lê o original pela referência autoritativa do documento.
traceparent transporta correlação W3C quando há uma requisição de origem.

Upload/reindexação gravam documento/geração e outbox na mesma transação. O
publicador usa confirmações e marca published_at após a publicação. Um crash
entre confirmação e commit pode repetir o evento. O worker serializa por UUID
e confirma chunks/indexed_generation atomicamente; ACK ocorre depois do commit.
Evento de geração antiga, já indexada ou documento excluído é confirmado sem
reescrever os chunks. Tenant divergente falha e segue retry/DLQ.

Mensagens e filas são duráveis/persistentes. As filas principais têm TTL de 24h.
Falhas usam delays 1/5/30s, header x-retry-count e quatro tentativas totais;
publicação do retry é confirmada antes do ACK original. Isso é **at-least-once**,
não exactly-once. Filas e seus estados não fazem parte do backup do banco/MinIO;
o restore rearma a outbox das gerações inacabadas, inclusive failed.

O consumer ainda aceita generation ausente como 1 para mensagens legadas; isso
não altera o contrato dos produtores novos. JSON/UUID inválidos e originais
irrecuperáveis chegam à DLQ após o orçamento. Não registre corpo nem credenciais
em logs. Reparo/replay devem preservar event_id, resetar tentativas e exigir opt-in:

```sh
python -m worker.replay_dlq --queue document.upload.dlq --limit 10
python -m worker.replay_dlq --queue document.upload.dlq --limit 10 --execute
```

Exclusão de objeto usa evento **interno da outbox** com routing_key storage.delete,
document_id nulo e payload `{"storage_key":"..."}`. Não é publicado no RabbitMQ;
o publicador executa remoção idempotente e persiste resultado/backoff. A exclusão
do documento/chunks no banco e o pedido de cleanup são atômicos. A remoção do
original é eventual; acompanhe a outbox, não trate DELETE 204 como confirmação
de que o storage já terminou. Reconciliação e replay têm limite e dry-run padrão.

Atualizações deste contrato exigem testar produtor, consumer, replay e restore;
as suítes de falha exercitam reconexão, redelivery, concorrência, rollback e crash.
