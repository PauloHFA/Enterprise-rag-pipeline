# Recuperação documental — etapa 3

## Extração e origem

O worker usa extração estruturada de PDF, DOCX, HTML, TXT e Markdown. Cada trecho
mantém página (quando existente), seção e posição no texto normalizado. Título
explícito em `metadata.title` prevalece sobre o título extraído; na ausência de
um título, usa-se o nome do arquivo sem extensão. Autor e formato são registrados
em `extraction_metadata` quando disponíveis.

- PDF: texto por página, numeração iniciando em 1 e título dos metadados do PDF.
- DOCX: título das propriedades/estilo Title, headings e tabelas na ordem original.
  Página é `null`: o arquivo não fornece paginação física confiável sem renderização.
- HTML: título, headings e blocos textuais, sem scripts/styles/navigation nem
  duplicação de texto de elementos aninhados. Tabelas viram linhas/células textuais.
- Markdown: headings ATX e hierarquia de seções; blocos de código, listas e tabelas
  permanecem no texto. Não há renderização de links ou reconstrução de layout.
- TXT: conteúdo UTF-8, incluindo BOM; título deriva do nome do arquivo.

A normalização aplica Unicode NFC, finais de linha e hifenização de quebra de
linha. Offsets são **índices de caracteres Unicode do texto normalizado da seção**,
com início inclusivo e fim exclusivo. Não são posições em bytes do arquivo
original. `section_idx` começa em zero e distingue seções com o mesmo título.
Chunks nunca atravessam uma página/seção.

## Chunking e limite do modelo

`semantic-token-chunker-v1` usa o tokenizer do próprio modelo, prefere fronteiras
de parágrafo/sentença e usa um corte por tokens quando o bloco é maior que o
orçamento. A sobreposição é configurada em tokens. O título e a seção prefixam
somente o texto de embedding; o conteúdo persistido mantém os offsets da origem.

O modelo padrão aceita **128 tokens** por entrada. O worker reserva espaço para
tokens especiais e prefixo de contexto (até 32 tokens); o conteúdo pode ter menos
tokens que o alvo configurado para caber nesse limite. Cada entrada é verificada
antes do encode, sem truncamento silencioso. Um tokenizer rápido com offsets é
necessário para esse caminho; uma configuração incompatível falha explicitamente.

Defaults:

```dotenv
CHUNK_TARGET_TOKENS=96
CHUNK_OVERLAP_TOKENS=12
EMBEDDING_BATCH_SIZE=32
OCR_ENABLED=false
OCR_LANGUAGES=por+eng
```

`CHUNK_SIZE` e `CHUNK_OVERLAP` em caracteres são parâmetros legados; o pipeline
novo usa os parâmetros em tokens. Alvos de 300–500 tokens do planejamento exigem
um modelo com janela maior; não devem ser usados para forçar o modelo atual.

## OCR opcional

A imagem do worker inclui Tesseract e os idiomas português/inglês. Com
`OCR_ENABLED=true`, páginas sem texto ou com menos de 32 caracteres e imagens
passam por OCR a 300 DPI. Isso contempla scans com apenas um rodapé textual.
`extraction_metadata.ocr_pages` registra as páginas processadas por OCR.
O OCR continua desligado por padrão e não garante precisão para escrita manual,
tabelas complexas ou imagens de baixa resolução.

## Embeddings em lote e estados

O processamento evolui por `queued → extracting → chunking → embedding → indexed`;
falhas usam `failed`. GET document retorna `processing_history`, com estado,
geração e timestamp. Em retries, a mesma geração pode repetir etapas.

O terminal anterior `completed` foi substituído por **`indexed`**. Atualize clientes
que aguardam esse status. O startup converte registros legados de completed para
indexed. Não execute workers da versão antiga junto com a nova.

O lote é por documento, limitado a `EMBEDDING_BATCH_SIZE` (1–256). Cada chamada
recebe vários chunks; formato, dimensão e finitude dos vetores são validados.
`extraction_metadata.embedding` contém quantidade de chunks/lotes, tamanho de
lote, tempo de encode, dimensão e limite de tokens do modelo. Não há batching
entre documentos ou benchmark de escala nesta etapa.

Chunks, proveniência, vetores e sucesso da geração continuam atômicos. Locks,
outbox, retry/DLQ e cleanup da etapa 2 permanecem ativos. Durante reindexação,
os chunks anteriores permanecem disponíveis até o novo conjunto ser confirmado.

## Busca e citações

Os três modos retornam `citation` com documento, arquivo, título, página, seção,
section_idx, offsets e excerpt. `excerpt` é texto real do chunk, limitado a 200
caracteres. O fim da citação corresponde ao trecho efetivamente retornado.
Resultados antigos sem proveniência têm offsets/página nulos; reindexe-os para
popular esses campos. Não se inventam posições para dados legados.

```json
{
  "query": "prazo de pagamento",
  "mode": "hybrid",
  "top_k": 5,
  "filters": {
    "tags": ["contratos", "financeiro"],
    "created_after": "2026-10-01T00:00:00Z"
  },
  "rrf": {"k": 60, "weights": {"lexical": 1.0, "vector": 1.0}}
}
```

Todas as tags indicadas devem estar presentes. Uma tag em string continua aceita
e vira uma lista de um item. Datas exigem timezone e intervalo ordenado. RRF aceita
k inteiro positivo até 10000 e pesos finitos não negativos, com pelo menos um
peso positivo. Consulta vazia, tipos incorretos e campos desconhecidos retornam
422. O filtro de tenant continua obrigatório e derivado da autenticação.

## Migração, testes e limites

O upgrade aditivo cria campos de título, extração/histórico e proveniência.
Chunks legados permanecem válidos; a migração não os reprocessa automaticamente.
Use POST `/v1/documents/{id}/reindex` para aplicar a estratégia nova. Migrações
reversíveis e promoção de produção permanecem na etapa 4.

O corpus de integração cria PDF de duas páginas, DOCX com heading/tabela, HTML,
TXT, Markdown, texto com muitos chunks e scans com/sem rodapé. Verifica origem,
três modos de busca, tags, estados, dimensão, lote e OCR reais. As medições são
salvas em `.test-results/document-corpus.json` e publicadas como artifact no CI.

Os testes de corpus usam alvo 48, overlap 6, lote 2, OCR ligado e rate limit 1000
para permitir verificações repetidas. Os defaults da aplicação permanecem 96/12/32,
OCR desligado e rate limit 60. A relevância comparativa e os benchmarks p95
continuam previstos na etapa 5; esse corpus não comprova qualidade de produção.
