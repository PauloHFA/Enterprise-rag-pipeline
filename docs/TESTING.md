# Execução e interpretação dos gates da etapa 5

Use Python 3.11, Docker Engine/Compose e um projeto **descartável**. Os testes de
resiliência param serviços, criam falhas, exercitam restore e alteram réplicas.
Não execute esses testes ou o benchmark sobre uma instalação com clientes.

## Qualidade e unitários

```sh
python -m pip install --upgrade pip==26.2.1 setuptools==84.0.0
pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu
pip install -e '.[api,worker,dev]'
ruff check api worker shared tests evaluation benchmarks scripts migrations
ruff format --check api worker shared tests evaluation benchmarks scripts migrations
mypy api worker shared evaluation benchmarks scripts --platform linux
python scripts/audit_dependencies.py
python -m pytest tests -q
```

Sem variáveis RAG_E2E os 23 testes que exigem infraestrutura são explicitamente
ignorados. Isso não constitui aprovação da integração. Copie o arquivo binário
`.coverage` para `.test-results/unit.coverage` antes de executar os cenários reais.
A auditoria consulta advisories públicos e exige rede; registra a versão base dos
wheels `+cpu`, todas as dependências transitivas e falha se algum pacote for ignorado.
Uma auditoria sem vulnerabilidades conhecidas não certifica toda a segurança da imagem.

## Serviços reais e cobertura combinada

No PowerShell, configure apenas credenciais de teste e use o separador `;` de
COMPOSE_FILE. No Linux, use `:` e `export NOME=valor` em vez de `$env:NOME=...`.

```powershell
$env:COMPOSE_FILE = 'docker-compose.yml;docker-compose.test.yml'
$env:API_KEY = 'ci-minimum-flow-key'
$env:TENANT_API_KEYS = '{"acme":"ci-tenant-acme-key"}'
$env:RAG_E2E_API_KEY = $env:API_KEY
$env:RAG_E2E_TENANT_KEY = 'ci-tenant-acme-key'
$env:POSTGRES_PORT = '55432'
$env:RAG_E2E_DATABASE_URL = 'postgresql://raguser:ragpass@localhost:55432/ragdb'
$env:RAG_E2E_URL = 'http://localhost:8000'
$env:RAG_MONITORING_URL = 'http://localhost:9090'
$env:RAG_E2E_TIMEOUT = '600'
$env:RAG_TEST_COMPOSE_PROJECT = 'enterprise-rag-ci'
$env:COMPOSE_PROJECT_NAME = 'enterprise-rag-ci'
$env:OCR_ENABLED = 'true'
$env:CHUNK_TARGET_TOKENS = '48'
$env:CHUNK_OVERLAP_TOKENS = '6'
$env:EMBEDDING_BATCH_SIZE = '2'
$env:RATE_LIMIT_PER_MINUTE = '10000'
docker compose -p enterprise-rag-ci --profile monitoring up -d --build --wait --wait-timeout 300
python -m pytest tests/test_integration.py tests/test_document_pipeline.py tests/test_operations.py tests/test_resilience.py --no-cov -v
python scripts/audit_dependencies.py --project enterprise-rag-ci --service api --output .test-results/api-dependency-audit.json
python scripts/audit_dependencies.py --project enterprise-rag-ci --service worker --output .test-results/worker-dependency-audit.json
python -m evaluation.run_eval --check
python scripts/collect_coverage.py --project enterprise-rag-ci
```

O override de testes usa o target `instrumented`. Cada processo Python grava seu
próprio shard no volume de cobertura, inclusive subprocessos de extração e comandos
administrativos. O coletor **para API, worker e outbox**, combina os shards com os
unitários, gera JSON/XML e exige **80% de linhas em cada um dos 19 módulos** listados
em `scripts/check_coverage.py`. Uma média alta não compensa um módulo abaixo da meta.
Os demais módulos continuam visíveis no relatório global. Não misture shards de
revisões diferentes: comece uma rodada nova com volume de cobertura vazio.

## Avaliação de recuperação

`evaluation/dataset.json` contém 20 documentos e 60 perguntas em português, com
graus de relevância explícitos (3 principal; 1 relacionado). São políticas
**sintéticas**, escritas para regressão, sem validação de especialistas da empresa.
Substitua ou acrescente consultas reais revisadas antes de homologar qualidade.
Não ajuste o gabarito em função dos rankings obtidos.

O avaliador ingere DOCX com identificador único fora do texto extraído, espera
`indexed`, aplica tag exclusiva por rodada e remove apenas os próprios documentos
ao terminar. Em cada modo, pede os primeiros dez **chunks**, deduplica documentos
preservando a ordem e calcula recall@1/5/10, MRR@10 e nDCG@10 (ganho `2^grau-1`).
Assim, dez chunks do mesmo documento não equivalem a dez documentos recuperados.

`--check` aplica pisos de regressão híbrida definidos no código: recall@10 ≥0,90,
MRR@10 ≥0,70 e nDCG@10 ≥0,75. Não são requisitos negociados com usuários nem prova
de superioridade da fusão híbrida. Os três modos são publicados no relatório,
inclusive quando a fusão empata com a busca vetorial.

## Benchmark com binários de produção

Depois da coleta de cobertura, mantenha o projeto descartável e configure:

```powershell
$env:COMPOSE_FILE = 'docker-compose.yml;docker-compose.test.yml;docker-compose.benchmark.yml'
$env:CHUNK_TARGET_TOKENS = '96'
$env:CHUNK_OVERLAP_TOKENS = '12'
$env:EMBEDDING_BATCH_SIZE = '32'
docker compose -p enterprise-rag-ci --profile monitoring up -d --build --wait --wait-timeout 300
python -m evaluation.run_eval --check --output .test-results/retrieval-default.json
python -m benchmarks.run_benchmark --check-latency
python -m scripts.demo
docker compose -p enterprise-rag-ci --profile monitoring down
```

O último override seleciona `production`, sem instalar coverage, e desativa sua
inicialização. O relatório registra esse fato. Não compare latências obtidas com
instrumentação ativa aos limites de produção.
A avaliação de relevância é repetida com os chunks/lotes padrão (96/12/32);
`retrieval.json` descreve o perfil de teste (48/6/2) e `retrieval-default.json`
descreve o perfil de produção. O CI audita também as duas imagens de produção.

O ensaio indexa 31 documentos pequenos, mede separadamente primeira requisição,
ingestão em lote de 30 documentos, 100 buscas por modo/concorrência (1 e 4) e
20 uploads por tamanho (4 KiB, 256 KiB, 1 MiB). Nos perfis de upload puro, os
workers são interrompidos e os jobs são cancelados antes de reiniciá-los; isso
mede aceitação HTTP/storage/outbox, não processamento de arquivos grandes.
Percentis usam nearest rank. O gate exige p95 upload <300 ms e híbrido <500 ms
nos perfis medidos; cold start aparece separado e não é descartado do relatório.

O script registra recursos Docker, versões de runtime, revisão do modelo, hashes
de fontes, amostras e erros. O corpus pequeno **não comprova a meta até um milhão
de chunks**. Repita a carga com distribuição real, TLS, volumes e hardware de
referência antes do aceite em escala. Não foi executado um teste de saturação.
O ensaio local usa o Compose de desenvolvimento com binários de produção, sem
as cotas CPU/memória do Compose prod/free. Repita também sob essas cotas para
dimensionar o servidor. Qualquer erro de busca, inclusive lexical/vetorial,
faz o gate falhar; o limite de latência se aplica ao modo híbrido.

## CI e evidências

`.github/workflows/ci.yml` reproduz qualidade → integração/avaliação/cobertura →
benchmark sem instrumentação e demonstração. Falhas bloqueiam o job e relatórios
JSON/XML são publicados como artifacts. O workflow remoto só pode ser declarado
aprovado após executar no GitHub, depois do commit/push dessas alterações.

Os relatórios locais selecionados estão em [reports](reports/). O
[relatório de aceite](ACCEPTANCE_REPORT.md) distingue resultados e pendências.
Saída completa, shards e relatórios HTML permanecem em `.test-results`, ignorado
pelo Git. Não versionar credenciais, arquivos privados de clientes ou dumps.
