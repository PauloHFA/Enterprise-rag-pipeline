# Credenciais e configurações para operar o sistema

## Situação deste checkout

O roteiro principal tem cinco etapas. A etapa 5 entrega ferramentas, medições e
gates; a homologação em escala e com dados reais está discriminada no relatório
[de aceite](ACCEPTANCE_REPORT.md). Não há uma sexta etapa de implementação no roteiro.
UI final, respostas geradas por LLM e Kubernetes são extensões fora deste MVP.

Foi criado **.env.production**, ignorado pelo Git, com cinco segredos aleatórios
distintos. O gerador usa criação exclusiva: **não substitui arquivos existentes
nem rotaciona senhas automaticamente**. Os valores completos ficam nesse arquivo
local. .env e .env.free preexistentes foram preservados. Não use as credenciais
fixas do Compose de desenvolvimento para publicar o serviço.

```powershell
# Na raiz enterprise-rag, com a .venv existente:
.venv/Scripts/python.exe scripts/configure.py check --file .env.production
# Para uma instalação nova onde o arquivo ainda não existe:
.venv/Scripts/python.exe scripts/configure.py init --file .env.production
```

O check mostra nomes/estado sem imprimir senhas. Ele verifica presença de
credenciais e arquivos, não substitui testes de DNS, certificado, rede e serviços.
O comando requer python-dotenv, instalado pelas dependências da API.

## Credenciais obrigatórias no Compose de produção/free

| Serviço | Usuário/identidade | Variável com o segredo | Onde obter | Estado local |
| --- | --- | --- | --- | --- |
| API REST | tenant `default`; header `X-API-Key` | `API_KEY` | Gerador local | Gerada em .env.production |
| PostgreSQL | usuário `raguser`, banco `ragdb` | `POSTGRES_PASSWORD` | Gerador local | Gerada em .env.production |
| RabbitMQ | usuário `raguser`, vhost `/` | `RABBITMQ_PASSWORD` | Gerador local | Gerada em .env.production |
| MinIO | access key / usuário `minioadmin` | `MINIO_ROOT_PASSWORD` | Gerador local | Gerada em .env.production |
| Grafana | usuário `admin` | `GRAFANA_PASSWORD` | Gerador local | Gerada em .env.production |

O Compose monta as URLs internas e propaga as credenciais da seguinte forma:

| Configuração da aplicação | Valor/construção dentro da rede Docker |
| --- | --- |
| `DATABASE_URL` | `postgresql://raguser:<POSTGRES_PASSWORD>@postgres:5432/ragdb` |
| `RABBITMQ_URL` | `amqp://raguser:<RABBITMQ_PASSWORD>@rabbitmq:5672` |
| `MINIO_ENDPOINT` | `minio:9000` |
| `MINIO_ACCESS_KEY` | `minioadmin` |
| `MINIO_SECRET_KEY` | mesmo segredo de `MINIO_ROOT_PASSWORD` |
| `MINIO_BUCKET` | `documents`, criado na inicialização da API |
| `MINIO_SECURE` | `false` na rede Docker; TLS público termina no Nginx |

Não é necessário inventar senhas diferentes para as variáveis derivadas acima.
Para trocar os serviços internos por banco/broker/storage externos, ajuste os
environments de **api, worker e outbox** em um override de Compose, incluindo TLS
e credenciais do provedor. Apenas adicionar DATABASE_URL ao arquivo env não
substitui as URLs declaradas no Compose atual. A conta de migração precisa das
permissões DDL/extensões descritas no [runbook de operação](SECURITY_OPERATIONS.md).

Senhas são persistidas também nos serviços. Alterar POSTGRES_PASSWORD em um env
não muda a senha de um banco que já tem volume inicializado. Use um projeto novo
para produção e faça rotação coordenada no serviço e na aplicação quando necessário.
Os valores gerados usam caracteres seguros para URLs, evitando problemas de escaping.

## Identidade opcional e múltiplos tenants

| Configuração | Quando usar | Quem fornece |
| --- | --- | --- |
| `TENANT_API_KEYS` | Um segredo independente por tenant, objeto JSON | Administrador, usando gerador criptográfico |
| `OIDC_ISSUER` | Validar access tokens JWT com issuer exato HTTPS | Provedor de identidade |
| `OIDC_AUDIENCE` | Identificador da API/resource no token | Cadastro da API no provedor |
| `OIDC_JWKS_URL` | Endpoint HTTPS das chaves públicas do issuer | Metadados confiáveis do provedor |
| `OIDC_TENANT_CLAIM` | Claim confiável do tenant; padrão `tenant_id` | Administrador do provedor |

Exemplo estrutural: `TENANT_API_KEYS={"acme":"<chave-exclusiva>","globex":"<outra-chave>"}`.
As chaves não podem se repetir entre tenants ou coincidir com API_KEY. O cliente
envia **X-API-Key ou Authorization: Bearer**, nunca ambos. A API não confia no
tenant informado pelo formulário para autorizar acesso.

JWT/OIDC é opcional: as chaves de API bastam para o sistema completo deste MVP.
O backend verifica RS256, assinatura, issuer, audience, exp e claims obrigatórios.
**Client secret OIDC e chave privada de assinatura não são credenciais deste
backend**: a chave privada fica no provedor. Login interativo e frontend não
estão implementados. O provedor externo ainda precisa ser configurado e homologado
por você caso escolha usar JWT; nenhum cadastro externo foi criado nesta tarefa.

## Configuração externa obrigatória para acesso público

| Item | O que configurar | Estado deste checkout |
| --- | --- | --- |
| Servidor | Linux/Docker Engine + Compose; disco persistente e memória para API/modelos/workers | Stack local exercitada; VM pública não provisionada |
| Domínio/DNS | Um nome seu apontando para o IP do servidor | Você precisa fornecer/configurar |
| Certificado | `certs/fullchain.pem`, cadeia confiável e SAN correspondente ao domínio | Certificado real não fornecido |
| Chave TLS | `certs/privkey.pem`, correspondente ao certificado | Chave real não fornecida |
| Rede | Permitir entrada 80/443; SSH restrito à sua administração | Configurar no firewall/cloud |
| Volumes | Persistir PostgreSQL, RabbitMQ, MinIO e modelos; monitoramento conforme retenção | Criados pelo Compose no servidor escolhido |
| Backup | Volume/local externo protegido, janela e agendamento, teste de restore | Ferramenta e ensaio prontos; destino/agendamento de produção são seus |

Obtenha o certificado com uma CA confiável (por exemplo, ACME/Let's Encrypt ou
CA interna confiada pelos clientes). Credenciais de DNS para emissão automática
são do seu provedor, não variáveis da aplicação. Não reutilize os certificados
temporários dos testes. O bootstrap confere expiração, confiança e correspondência
da chave; renovação e reload do Nginx precisam de agendamento no servidor.

## Implantação com isolamento dos volumes de desenvolvimento

Execute na raiz do repositório, depois de preencher DNS/certificados:

```sh
docker compose -p enterprise-rag-prod --env-file .env.production -f docker-compose.prod.yml config --quiet
docker compose -p enterprise-rag-prod --env-file .env.production -f docker-compose.prod.yml up -d --build --wait --wait-timeout 300
```

Para uma réplica de worker, use docker-compose.free.yml. O bootstrap Linux aceita
`bash scripts/bootstrap-free.sh .env.production` e usa o projeto enterprise-rag-free
(ou RAG_COMPOSE_PROJECT). Use sempre os mesmos argumentos de projeto, env e arquivo
para status, logs, backup, manutenção, escala e desligamento. `down` sem `-v`
preserva volumes; não remova volumes para tentar corrigir credenciais de uma instalação real.

| Acesso | Endereço |
| --- | --- |
| API | `https://<seu-dominio>/v1/...` |
| OpenAPI | `https://<seu-dominio>/docs` |
| Liveness/readiness | `/healthz` e `/readyz` |
| Grafana | `http://127.0.0.1:3000` no servidor, por túnel SSH |
| Prometheus | `http://127.0.0.1:9090` no servidor, por túnel SSH |
| PostgreSQL/RabbitMQ/MinIO | Rede Docker; sem portas públicas na produção |

Prometheus não exige senha no arranjo local isolado; proteja o acesso por SSH/firewall.
O proxy público recusa /metrics. As consoles de RabbitMQ/MinIO também não têm
portas públicas na produção. Os alertas são avaliados no Prometheus. Email,
Slack ou outro receiver exigem configuração adicional e seu próprio segredo
de webhook/SMTP; não são necessários para ingestão e busca.

## Parâmetros funcionais e operacionais

| Variável | Padrão / decisão |
| --- | --- |
| Modelo | paraphrase-multilingual-MiniLM-L12-v2, dimensão fixa 384 |
| `EMBEDDING_MODEL_REVISION` | `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, pin da revisão avaliada |
| `CHUNK_TARGET_TOKENS` / `CHUNK_OVERLAP_TOKENS` | 96 / 12; janela do modelo 128 inclui contexto e tokens especiais |
| `EMBEDDING_BATCH_SIZE` | 32 |
| `OCR_ENABLED` / `OCR_LANGUAGES` | false / por+eng; habilite para PDFs digitalizados |
| `MAX_UPLOAD_BYTES` | 20 MiB; proxy limita envelope a 21 MiB |
| `EXTRACTION_TIMEOUT_SECONDS` | 120 segundos |
| `RATE_LIMIT_PER_MINUTE` | 60 por IP do peer e por réplica; o ensaio usa limite elevado, não o copie sem análise |
| `RETENTION_DAYS` | 0 desabilita expiração; defina conforme sua política documental |
| `OUTBOX_RETENTION_DAYS` | 7 para eventos já publicados; pendentes são preservados |
| Retries | 1/5/30 segundos, quatro tentativas no total antes da DLQ |

Não são necessárias chaves OpenAI, Azure OpenAI nem outra API paga: os embeddings
rodam localmente em CPU. O modelo é público; token Hugging Face não é obrigatório
para esse modelo. A primeira preparação precisa de acesso de saída ao repositório
de modelos e aos registries/dependências. O volume de modelos evita baixar pesos
novamente. Faça uma busca de aquecimento antes de liberar tráfego: a primeira
carga do modelo teve latência de segundos no ensaio, separada do p95 aquecido.

Agende `python -m api.retention` via serviço administrativo/cron usando o environment
de produção; cada invocação exclui até 100 documentos expirados. Em Compose, use
`run --rm --no-deps api python -m api.retention` com os mesmos argumentos globais.
Defina prazo e frequência antes de ativar. Backup requer interromper escritores
e usar diretório persistente novo; restore exige destino vazio e isolado. Siga o
[procedimento testado](SECURITY_OPERATIONS.md#backup-e-restauração).

## Verificação antes de liberar clientes

1. Validar configuração, certificado e DNS; iniciar com --wait.
2. Conferir /readyz e logs de migração; aquecer a busca vetorial.
3. Executar a demonstração com chaves de um tenant de teste: upload → indexed →
   três buscas/citações → exclusão. Nunca enviar a chave em query string.
4. Conferir dashboard, filas e outbox; ensaiar backup/restore isolado.
5. Homologar o dataset real e a carga de produção, inclusive escala pretendida.

Geração de credenciais/configuração não significa que um domínio público, um
provedor OIDC, um backup remoto ou notificações externas já foram implantados.
