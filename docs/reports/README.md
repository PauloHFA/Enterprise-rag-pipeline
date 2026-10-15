# Evidências locais — 15/10/2026

Relatórios de serviços reais do projeto descartável `rag-flow-review`. Todos os
documentos são sintéticos e removidos pelos scripts. Não há segredos, documentos
de clientes, dumps ou certificados neste diretório.

| Arquivo | Conteúdo |
| --- | --- |
| retrieval-default.json | 60 consultas; chunks/overlap/batch padrão 96/12/32; binários de produção |
| retrieval.json | Mesmas consultas; perfil de teste 48/6/2, com instrumentação |
| benchmark.json | Amostras, percentis, baseline, primeiras requisições e recursos; binários de produção sem coverage |
| domain-coverage.json | Gate ≥80% para cada um dos 19 módulos |
| coverage-summary.json | Resumo de todos os módulos API/worker/shared, cobertura combinada |
| dependency-audit.json | Snapshot e advisories das dependências do host |
| api-dependency-audit.json / worker-dependency-audit.json | Dependências das imagens instrumentadas |
| api-production-dependency-audit.json / worker-production-dependency-audit.json | Dependências das imagens de produção após atualização do pip |
| manifest.json | Contagem de testes, hashes dos relatórios/fontes e proveniência |

O commit base registrado nos relatórios é `05bbddf`, com working_tree_dirty=true:
**a etapa 5 ainda não estava commitada ao medir**. Os hashes permitem identificar
as fontes da medição; não associe esses resultados ao commit base sem as mudanças.
O manifesto não inclui seu próprio hash nem arquivos dentro deste diretório como
fontes. Uma alteração posterior no checkout pode exigir novo manifesto/medição.

O benchmark antecedeu a atualização apenas do instalador pip nas imagens e a
exigência de zero erros também nos modos lexical/vetorial. Os hashes dos módulos
de runtime/requirements registrados no benchmark foram conferidos; todas as 600
buscas medidas tiveram zero erros, satisfazendo o gate mais estrito. Os scripts
de benchmark agora registram também a ressalva de cotas CPU/memória. As condições
reais do relatório anterior continuam explicitadas no [aceite](../ACCEPTANCE_REPORT.md).

Não foram medidos um milhão de chunks, tráfego com TLS, cotas de produção nem
uma VM independente limpa. A avaliação é sintética, não homologação empresarial.
As primeiras requisições lentas foram preservadas. A auditoria foi consultada na
data; novos advisories podem mudar seu resultado. O CI remoto não foi executado
para alterações ainda locais.

Logs completos, dados binários de coverage e HTML estão em `.test-results`/htmlcov,
ignorados pelo Git. Para reproduzir cada gate, siga [TESTING.md](../TESTING.md).
