# Operação

O guia vigente é [Segurança e operação](../docs/SECURITY_OPERATIONS.md).

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
