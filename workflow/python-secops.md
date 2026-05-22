---
description: Configurar ambiente Python moderno com UV, Ruff, Pyright e auditoria de segurança (SecOps)
---

# Workflow: Modern Python Stack (UV + Ruff + Pyright + Security)

## Diretiva Principal
Atue como um Engenheiro Python Sênior e Especialista em Segurança (SecOps). Sua missão é estruturar, formatar, auditar e blindar o repositório atual utilizando as ferramentas mais modernas do ecossistema Python: `uv` (para gestão ultrarrápida e determinística de dependências) e `ruff` (para linting, formatação e análise estática de segurança).

## Plano de Execução (Siga em Ordem)

// turbo-all
1. **Gestão de Ambiente e Trava de Dependências (UV):**
   - Verifique a existência de um `pyproject.toml` ou `uv.lock`. Se não existir, inicie o projeto de forma autônoma utilizando `uv init`.
   - Certifique-se de que o `uv.lock` esteja atualizado para garantir builds determinísticos e evitar ataques de *dependency confusion*.
   - Adicione as ferramentas de infraestrutura: `uv add --dev ruff pyright pytest pip-audit`

2. **Aplicação das Melhores Práticas e Regras de Segurança (Pyproject Config):**
   - Valide se o `pyproject.toml` contém as configurações do Ruff. Caso contrário, adicione as seguintes regras:
     - `line-length = 88`
     - `select = ["E", "F", "I", "UP", "B", "SIM", "C90", "S", "T20"]` 
     - Ative a formatação do Ruff (`[tool.ruff.format]`).

3. **Auditoria, Formatação e Varredura de Vulnerabilidades:**
   - Executar formatação: `uv run ruff format .`
   - Executar linting: `uv run ruff check --fix .`
   - Checar de segurança: vulnerabilidades e dependências `uv run pip-audit`
   - Tipagem estática estrutural: `uv run pyright`

4. **Varredura de Segredos e Chaves:**
   - Inspecione autonomamente o código em busca de chaves hardcoded e segredos vazados.
   - Adicione `.env` no `.gitignore`.

5. **Entrega via Artifacts:**
   - Gere um relatorio final dos problemas encontrados (Artifact).
