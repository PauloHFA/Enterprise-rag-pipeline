# Etapa 4 — segurança e operação

Para o inventário completo de segredos, DNS/TLS e parâmetros, consulte
[Configuração de implantação](DEPLOYMENT_CONFIGURATION.md). As medições mais
recentes e pendências de homologação estão no [aceite da etapa 5](ACCEPTANCE_REPORT.md).

## Upload e extração

O limite padrão é 20 MiB por arquivo e mais 64 KiB para o envelope multipart.
O middleware mede o corpo inclusive sem Content-Length, antes de executar o
parser multipart. O handler também faz leitura limitada. Excesso retorna 413;
extensão, MIME e conteúdo incompatíveis retornam 415. PDF usa assinatura;
DOCX exige um ZIP com as entradas OpenXML, até 2.000 entradas e 100 MiB
descompactados. Texto deve ser UTF-8, sem controles binários. HTML tem detecção
de tags; Markdown é texto identificado pela extensão. Isso não é antivírus nem
prova de que um documento com assinatura válida pode ser extraído.

Nomes são reduzidos ao basename. O tenant autenticado define o prefixo do
objeto; o nome enviado não pode escolher diretórios. Documentos vazios retornam
400. Metadados continuam exigindo objeto JSON.

API, publicador e worker executam com UID 10001, filesystem somente leitura,
capabilities removidas, no-new-privileges e /tmp limitado. Apenas o inicializador
do volume do modelo usa root, sem rede, para ajustar sua propriedade. A extração
roda em subprocesso separado: timeout de 120s, CPU de 90s, espaço de endereçamento
de 1 GiB, até 1.000 páginas PDF e 2 milhões de caracteres extraídos. Limites de
CPU/memória usam resource no Linux; no Windows permanece o timeout do processo.
O subprocesso compartilha a rede e a identidade do container; não é uma VM nem
um sandbox de rede. Falhas seguem o retry/DLQ da etapa 2.

Variáveis: MAX_UPLOAD_BYTES, MAX_MULTIPART_OVERHEAD_BYTES,
MAX_DOCX_EXPANDED_BYTES, EXTRACTION_TIMEOUT_SECONDS, EXTRACTION_CPU_SECONDS,
EXTRACTION_MEMORY_MB, MAX_PDF_PAGES e MAX_EXTRACTED_CHARS. O Compose expõe os
ajustes mais comuns; os demais podem ser adicionados ao environment do serviço.
No proxy, client_max_body_size é 21m e deve acompanhar alterações do limite.

## Identidade e isolamento

X-API-Key continua disponível. API_KEY identifica o tenant default;
TENANT_API_KEYS é um objeto JSON tenant→chave, com chaves únicas. Identidades
aceitam letras/números, ponto, underscore e hífen, até 128 caracteres. A API
recusa inicializar sem autenticação configurada.

Para usar access tokens assinados, configure OIDC_ISSUER, OIDC_AUDIENCE e
OIDC_JWKS_URL. Issuer e JWKS devem usar HTTPS com certificado confiável. Somente
RS256 é aceito. Assinatura, issuer, audience e exp são verificados; exp, iat,
sub e o claim OIDC_TENANT_CLAIM (padrão tenant_id) são obrigatórios. O claim deve
ser administrado pelo provedor de identidade. Não há descoberta automática nem
fluxo de login no backend. JWKS usa cache de cinco minutos e atualização para
kid desconhecido. Uma falha de conexão ao provedor retorna 503; token inválido
retorna 401. Bearer e X-API-Key juntos são recusados.

Todas as operações e os três modos de busca filtram o tenant autenticado.
Consultar, excluir ou reindexar UUID de outro tenant retorna 404. Um filtro
tenant_id divergente retorna 403. Não há papel global de administrador via API.
O limitador em memória é por IP do peer, com expiração e capacidade limitada;
é local a cada réplica. Nginx também limita por IP e não aceita forwarded headers
como identidade. Quotas globais por tenant entre réplicas exigem armazenamento
compartilhado e não são prometidas por este limitador.

## Migrações

shared/models.py é a fonte única do esquema; api.models e worker.models apenas
reexportam as classes. A API executa Alembic até head na inicialização, sob
advisory transaction lock para serializar réplicas. O usuário do banco precisa
de permissões DDL/extensões. Para separar migração de runtime em outro ambiente,
execute a CLI com o usuário de migração antes de iniciar as aplicações.

```sh
python -m alembic current
python -m alembic upgrade head
python -m alembic check
```

Para desenvolver uma nova alteração, gere a revisão no checkout gravável com
`python -m alembic revision --autogenerate -m descricao`, revise o SQL e teste
upgrade/downgrade em banco descartável. As imagens de runtime são somente leitura.

0001 estabelece o baseline, adotando tabelas legadas por IF NOT EXISTS.
0002 adiciona outbox, geração e proveniência, convertendo completed→indexed
e unificando título em TEXT, inclusive em instalações criadas com VARCHAR.
Não use stamp head para pular a atualização. A dimensão versionada do vetor é
384; trocar de dimensão exige uma nova migração e reindexação. Downgrade para
0001 remove outbox e proveniência; downgrade para base remove as tabelas.
Faça backup e interrompa escritores antes de qualquer downgrade. Esquemas
legados modificados manualmente precisam de comparação/revisão prévia.

## Métricas, logs e alertas

```sh
docker compose --profile monitoring up -d --build --wait
```

API publica /metrics em 8000, worker em 9101 e publicador em 9102. Não há porta
de métricas do worker fixada no host, permitindo réplicas. Prometheus descobre
os IPs dos serviços por DNS e coleta cada réplica. Contadores registram uploads
novos, reindexações, exclusões, buscas, publicação confirmada, resultados do
worker, retries, DLQ e chunks. Histogramas medem HTTP, buscas, extração e total
do worker; gauges registram consumidor conectado e tamanho/idade da outbox.
Documentos, consultas, credenciais e tenants não viram labels.

Grafana provisiona datasource e dashboard Enterprise RAG Operations.
Prometheus avalia alertas de worker indisponível, outbox antiga, DLQ e erros 5xx.
Os alertas ficam disponíveis na interface/API do Prometheus; não há receiver
externo de email/Slack configurado. Os dados do Prometheus têm retenção de
15 dias e limite de 1 GiB.

Logs da aplicação são JSON. O header W3C traceparent válido preserva trace_id;
cada requisição/consumo recebe um novo span_id. A resposta retorna traceparent
e X-Request-ID; a outbox carrega o contexto até o worker. Logs de HTTP e conclusão
de indexação permitem correlação sem registrar conteúdo/chaves/tokens. Trata-se
de correlação e tempos nos logs/métricas, sem exportador de spans para Jaeger,
Tempo ou serviço externo. Logs nativos do Uvicorn/infra mantêm seu formato.

## Produção e free

Os dois arquivos são configurações completas, não overrides. Exigem
POSTGRES_PASSWORD, RABBITMQ_PASSWORD, MINIO_ROOT_PASSWORD, API_KEY e
GRAFANA_PASSWORD. Use senhas aleatórias próprias; nos campos inseridos em URLs,
use caracteres seguros de URI (por exemplo, token_urlsafe). Não reutilize os
valores de desenvolvimento. Não versione o arquivo de segredos.
O template é .env.free.example; .env.free e demais arquivos .env.* locais
são ignorados pelo Git, preservando os arquivos existentes no computador.

```sh
docker compose --env-file .env.production -f docker-compose.prod.yml config --quiet
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build --wait
```

Somente 80/443 são publicados externamente. Banco, broker, storage e API ficam
na rede Docker. Grafana 3000 e Prometheus 9090 são publicados somente em loopback;
acesse por túnel SSH. Certificados confiáveis devem existir em certs/fullchain.pem
e certs/privkey.pem; o Nginx falha se estiverem ausentes. HTTP redireciona para
HTTPS, exceto /healthz, e /metrics é bloqueado no proxy. O certificado de teste
é temporário e não é instalado para produção. Renovação e reload do Nginx são
responsabilidade do ambiente de implantação.

Produção inicia duas réplicas de worker; free inicia uma. Escale com
`docker compose ... up -d --scale worker=2 --scale outbox=2`; os serviços não têm
container_name nem host port fixos para esses processos. Limites de memória/CPU
estão nos arquivos; ajuste à máquina e ao custo do OCR. Os Compose de produção/free
usam limite de 2 GiB para a API: no ensaio de desenvolvimento, a API com modelo
carregado chegou a cerca de 1,04 GiB, excedendo o limite antigo de 1 GiB.
Essa observação não é dimensionamento de carga nem benchmark.
Os Compose de produção/free
foram preparados para operação, mas sua implantação pública exige as credenciais,
certificado e domínio reais do ambiente.

## Retenção

RETENTION_DAYS=0 mantém documentos indefinidamente. Um valor positivo faz o
comando a seguir excluir documentos pela data original de criação, em lotes de
100, usando o mesmo lock da exclusão e gerando cleanup durável do objeto:

```sh
python -m api.retention
```

Agende o comando no ambiente de operação; ele não é executado automaticamente
na API. Eventos já publicados são purgados após OUTBOX_RETENTION_DAYS (7 dias).
Eventos pendentes não expiram por essa rotina. Backups são separados desta
retenção e devem ter acesso, criptografia e prazo definidos pelo operador.

## Backup e restauração

O comando exige pg_dump/pg_restore da versão major 15, disponíveis na imagem
da API, e acesso ao banco/MinIO. Não faz fallback silencioso nem pula serviços.
Use um diretório novo num volume persistente. O snapshot inclui dump custom,
todos os objetos, content types, tamanhos e SHA-256, contagens de documentos,
chunks e outbox, e manifesto gravado somente ao final. Falha em componente ou
objeto referenciado ausente encerra com código diferente de zero.

Pare API, workers e publicadores antes da cópia para ter consistência entre
banco e storage. Um exemplo no Compose de desenvolvimento (use os mesmos
argumentos de projeto/arquivo/env do seu ambiente em todos os comandos):

```sh
docker compose stop api worker outbox
docker compose run --rm --no-deps --user 0:0 -v rag-backups:/backups api python -m api.backup backup /backups/snapshot-001 --quiesced
docker compose start api worker outbox
```

--quiesced declara que os escritores foram interrompidos; a ferramenta não
para outros processos por conta própria. Em caso de falha, reinicie os serviços
e investigue; não promova o diretório incompleto a backup válido. Uma operação
de backup requer janela de manutenção; ela não inclui memória do broker.
A restauração rearma a outbox da geração atual de cada documento ainda não
indexado, criando um evento quando o anterior já foi purgado. Assim recupera
mensagens que estavam apenas no broker. Documentos failed também recebem uma
nova sequência limitada de tentativas; a DLQ original não faz parte do snapshot.

Configure DATABASE_URL, MINIO_ENDPOINT e MINIO_BUCKET para um destino isolado
vazio e execute no processo administrativo com o volume de backup montado:

```sh
python -m api.backup verify /backups/snapshot-001
python -m api.backup restore /backups/snapshot-001 --isolated
```

O restore recusa banco com tabelas públicas e bucket com objetos. Valida todos
os checksums antes de gravar; pg_restore usa exit-on-error. Depois compara
contagens e lê cada objeto restaurado para conferir SHA-256. Falha durante o
restore pode deixar o destino parcial: descarte/recrie esse destino isolado
antes de repetir. Só redirecione tráfego após subir API/worker/publicador e
validar buscas/documentos. Restore não sobrescreve a instalação original.

## Validação reproduzível

Unitários: tests/test_security.py junto às suítes anteriores. Para o ensaio real,
configure as variáveis E2E descritas no roteiro, RAG_TEST_COMPOSE_PROJECT,
RAG_E2E_TENANT_KEY (chave de um segundo tenant) e RAG_MONITORING_URL. Execute:

```sh
python -m pytest tests/test_operations.py --no-cov -v
```

Esse ensaio verifica isolamento nos três modos de busca, uploads recusados,
traces/métricas, migração nova e adoção legada após downgrade, processos sem
root, proxy HTTPS, alerta disparado/recuperado, retenção e backup/restore com
recusa de snapshot incompleto/destino ocupado. Bancos, bucket, volume de backup,
certificado e proxy temporários pertencem ao teste; são removidos ao final.
O resultado do restore é salvo em .test-results/backup-restore.json e publicado
como artifact no CI. Resultados locais/remotos efetivos constam no roteiro.

Referências de implementação: [Alembic — connection sharing](https://alembic.sqlalchemy.org/en/latest/cookbook.html#sharing-a-connection-across-one-or-more-programmatic-migration-commands)
e [PyJWT — validação de claims](https://pyjwt.readthedocs.io/en/stable/usage.html).
