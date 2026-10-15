# Etapa 5 — resultado e limites do aceite

Data: **15/10/2026**. O roteiro de correção tem cinco etapas; esta encerra a
implementação prevista. **Não resta uma sexta etapa no roteiro principal.**
A homologação com dados e infraestrutura de produção continua pendente nos
pontos abaixo. Não foi publicada uma instalação em domínio público.

## Entrega e verificação local

- Avaliador de recuperação com dataset versionado, métricas por consulta e três
  modos, pisos explícitos de regressão e limpeza dos documentos da própria rodada.
- Benchmark reproduzível, parâmetros/versões registrados, amostras brutas,
  percentis, erros, throughput e primeira carga separados do cenário aquecido.
- Cobertura dos processos reais em imagens de teste; imagens de produção não
  instalam coverage. Gate de 80% por módulo, lint, formatação, tipagem e auditoria
  do snapshot completo de dependências, incluindo wheels CPU.
- Dependências vulneráveis atualizadas, revisão do modelo fixada e ORM tipado.
  Busca síncrona executada fora do event loop; locks evitam cargas duplicadas
  e uso concorrente inseguro do tokenizer/modelo compartilhado.
- Runbooks, ADRs, contrato JSON de eventos, demonstração executável e gerador de
  configuração privada. `.env.production` foi criado sem alterar os envs existentes.

Resultados efetivamente executados neste checkout:

| Verificação | Resultado |
| --- | --- |
| Unitários | **156 aprovados**; 23 testes reais ignorados nesta rodada sem infraestrutura |
| Integração real | **23 aprovados**, com PostgreSQL, RabbitMQ, MinIO e embeddings reais; 524,96 s |
| Backup/restore após ajuste da instrumentação administrativa | Revalidado separadamente: 1 aprovado |
| Demonstração | Upload → indexed → três buscas com citações → exclusão, nos dois perfis de chunking |
| Cobertura combinada API/worker/shared | **95,27%** (1.613/1.693 linhas); unitária isolada 85,00% |
| Gate dos 19 módulos de domínio | **Todos ≥80%**; menor resultado 89,71% em api/security.py |
| Ruff lint e formatação | Aprovados |
| Mypy, plataforma Linux | Aprovado em 43 arquivos de código |
| Auditoria Python do host | 126 pacotes; zero vulnerabilidades conhecidas, zero ignorados |
| Auditoria das imagens de produção | API: 79 pacotes; worker: 65; zero vulnerabilidades conhecidas/ignorados |
| Auditoria das imagens instrumentadas | API: 80; worker: 66; mesmo resultado |
| Compose prod/free | Configurações validadas, sem implantar domínio público |
| CI no GitHub | Workflow com gates preparado; execução remota aguarda commit/push |

Cobertura é de linhas, combinando unitários Windows com processos Linux da mesma
fonte. Não é cobertura de branches nem comprovação de ausência de bugs. Os
19 módulos estão explicitamente listados em `scripts/check_coverage.py`; módulos
de configuração e instrumentação também aparecem no total, sem omitir falhas do gate.
Auditoria refere-se às dependências Python e ao feed consultado na data; não
inclui análise binária dos wheels ou inventário de vulnerabilidades do SO.

## Qualidade da recuperação

20 políticas **sintéticas**, 60 consultas em português e gabarito graduado;
não são documentos reais da empresa nem julgamentos homologados por seus usuários.
Perfil padrão de produção: chunks/overlap 96/12, batch 32, modelo multilingual
MiniLM-L12-v2, revisão `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`; 20 chunks indexados.
OCR habilitado no ambiente de ensaio, embora esse corpus DOCX não use OCR.

| Modo | recall@1 | recall@5 | recall@10 | MRR@10 | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Lexical | 0,3000 | 0,3000 | 0,3000 | 0,3000 | 0,3000 |
| Vetorial | 0,7417 | 0,9500 | 0,9833 | 0,9097 | 0,9212 |
| Híbrido | 0,7583 | 0,9500 | **0,9833** | **0,9181** | **0,9274** |

No perfil de teste 48/6/2 (42 chunks), vetorial/híbrido empataram: recall@10
0,9667, MRR@10 0,9444 e nDCG@10 0,9439; lexical ficou em 0,2667. Os rankings e
scores por consulta dos dois perfis estão versionados. A variação mostra por que
é necessário avaliar parâmetros; não permite concluir superioridade geral de um modo.

Métricas usam documentos únicos presentes nos primeiros dez chunks retornados.
Pisos híbridos de regressão: recall@10 ≥0,90; MRR@10 ≥0,70; nDCG@10 ≥0,75.
Ambos os perfis passaram. Não houve ajuste dos pesos/modelo para melhorar o gabarito
após a medição. Próxima homologação: 50–200 perguntas reais, juízes do domínio e
holdout independente, com autorização para usar seus documentos.

## Desempenho observado

Docker Linux/WSL2 amd64, 16 CPUs virtuais disponíveis, 16.710.299.648 bytes de RAM
disponíveis ao Docker, Python 3.11, torch 2.14.1+cpu, sentence-transformers 6.1.0,
transformers 5.19.0. Compose de desenvolvimento com **binários de produção sem
coverage**, uma API/um worker, sem as cotas CPU/memória do Compose prod/free.
Chunking 96/12 e lote 32, limite de requisições elevado para o ensaio.
Medição direta HTTP local, sem Nginx/TLS. Datas/versões/hashes constam no JSON.

| Medida | Resultado |
| --- | ---: |
| Upload p95, 4 KiB / 256 KiB / 1 MiB, 20 amostras por perfil | 19,69 / 33,51 / **96,12 ms** |
| Upload p99, 1 MiB | 353,41 ms |
| Busca lexical p95, concorrência 1 / 4 | 7,05 / 8,08 ms |
| Busca vetorial p95, concorrência 1 / 4 | 33,28 / 188,13 ms |
| Busca híbrida p95, concorrência 1 / 4 | 48,21 / **145,47 ms** |
| Ingestão de 30 TXT de 4 KiB, 360 chunks | 16,59 s; **108,52 docs/min e 21,70 chunks/s** |
| Corpus de busca | 31 documentos / 372 chunks |
| Amostras de busca | 100 por modo/concorrência; 600 no total; zero erros |
| Primeira requisição de upload | **2.057,58 ms** |
| Primeira busca, com carga do modelo | **7.600,79 ms** |
| Primeiro documento, upload até indexed | 7,81 s |

Os percentis aquecidos passaram nos perfis medidos. As primeiras requisições
excederam os limites; devem ser consideradas no provisionamento e no aquecimento
antes de liberar tráfego. A readiness atual não carrega o modelo de busca.
O relatório bruto preserva essas primeiras requisições. Não há evidência de
estabilidade sob carga prolongada, de saturação ou de comportamento até um milhão
de chunks. O baseline de TXT não deve ser extrapolado para PDF/OCR ou documentos grandes.

## Confronto com os requisitos não funcionais

| Requisito | Evidência e estado |
| --- | --- |
| RNF-01: upload p95 <300 ms | Atendido apenas nos perfis aquecidos até 1 MiB; cold start e envelope máximo de 20 MiB exigem homologação adicional |
| RNF-02: híbrido p95 <500 ms até 1 milhão de chunks | Atendido no corpus pequeno; **escala de um milhão não comprovada** |
| RNF-03: baseline de ingestão | Medido acima, com recursos/corpus explícitos |
| RNF-04: escalar workers sem código | Cenário real de duas réplicas de worker/publicador aprovado |
| RNF-05: at-least-once/idempotência | Falhas, reentrega, retry, DLQ/replay, outbox e commit interrompido aprovados; não é exactly-once |
| RNF-06: API disponível com worker fora | Testes de falha e perfis de upload com workers parados; backlog é recuperado |
| RNF-07: Compose reproduzível | Builds Linux e subida local aprovados; runner limpo do CI e servidor público ainda não exercitados nesta revisão |
| RNF-08: ≥80% por módulo de domínio | Aprovado nos 19 módulos, combinando unitários e processos reais |

Os RF-01 a RF-10 têm cobertura funcional nos cenários de pipeline, corpus,
isolamento e operações: formatos, proveniência, chunks/lotes, três buscas/filtros,
status, deduplicação, reindexação, exclusão e citações. DOCX não recebe paginação
física inferida; offsets são do texto normalizado. Janela do modelo exige chunks
menores que a sugestão genérica inicial; justificativa em [ADRs](ADRS.md).

## O que falta para liberar produção

1. Commit/push e execução do workflow remoto com seus artifacts. Os resultados
   locais acima não são apresentados como resultado do GitHub Actions.
2. Disponibilizar servidor, domínio/DNS e certificado/chave TLS reais; implantar
   em projeto/volumes separados. As cinco credenciais já estão geradas somente
   em `.env.production`, ignorado pelo Git; valores não constam destes relatórios.
3. Configurar destino externo/janela de backup e agendamento de retenção, renovação
   TLS e, se desejado, receiver de alertas e provedor OIDC.
4. Homologar consultas reais e carga com TLS, cotas de produção, cold starts,
   arquivos maiores e escala pretendida; registrar desvios ou dimensionar recursos.

Esses itens são verificações e decisões operacionais pendentes, com ferramentas
implementadas; não constituem uma sexta etapa do roteiro. UI final, geração de
respostas com LLM e Kubernetes continuam fora deste MVP de ingestão e recuperação.

Evidências: [reports](reports/README.md). Reprodução: [TESTING.md](TESTING.md).
Inventário completo de credenciais e configurações:
[DEPLOYMENT_CONFIGURATION.md](DEPLOYMENT_CONFIGURATION.md).
