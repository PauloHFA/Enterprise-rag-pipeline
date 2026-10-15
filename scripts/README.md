# Operação

O guia vigente é [Segurança e operação](../docs/SECURITY_OPERATIONS.md).
Todas as credenciais e configurações estão no
[guia de implantação](../docs/DEPLOYMENT_CONFIGURATION.md); os gates e limites
das medições estão em [Testes](../docs/TESTING.md).

- `python scripts/configure.py init --file .env.production`: gera cinco segredos
  distintos em arquivo privado ignorado pelo Git; recusa substituir arquivo existente.
- `python scripts/configure.py check --file .env.production`: informa presença das
  configurações e certificados sem imprimir valores secretos.
- `python -m scripts.demo`: demonstra upload, indexação, três buscas/citações e
  exclusão; requer RAG_E2E_URL e RAG_E2E_API_KEY de um tenant de teste.
- `python scripts/audit_dependencies.py`: audita o snapshot completo instalado;
  `--project PROJETO --service api|worker` coleta o snapshot do container real.
- `python scripts/collect_coverage.py --project PROJETO`: para serviços descartáveis,
  combina cobertura real/unitária e exige 80% em cada módulo de domínio. Use somente
  após os testes, com o override instrumentado e `.test-results/unit.coverage`.

- `bootstrap-free.sh [arquivo-env]`: valida pré-requisitos, segredo/certificado e
  inicia o Compose free com `--wait`. Execute na raiz do repositório; não instala
  Docker nem cria certificados ou senhas automaticamente.
- `test-deploy.sh [URL]`: smoke test HTTPS com validação de certificado.
  Requer API_KEY no ambiente; use CURL_CA_BUNDLE para uma CA privada confiável.
- `backup.sh DIRETORIO --quiesced`: wrapper de `python -m api.backup backup`.
- `restore.sh DIRETORIO --isolated`: wrapper de `python -m api.backup restore`.

Backup/restore requerem dependências Python e clientes PostgreSQL major 15.
A imagem da API já contém esses componentes. Use volume persistente e siga a
janela de manutenção/destino vazio do guia. `mc` e AWS CLI não são utilizados.
Os comandos retornam erro se faltar qualquer componente obrigatório.
