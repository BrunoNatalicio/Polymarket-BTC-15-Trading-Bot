---
description: Gerenciar, Instalar e Executar o CI/CD Harness local
---

# 🛡️ CI/CD Harness Engineering Workflow

Este workflow administra o gatilho de *pre-merge-commit* agnóstico. Siga estes passos a pedido do usuário:

## 1. Instalar os Hooks de Segurança
Se o usuário pediu para "Ativar CI", "Instalar Hooks" ou se a IA precisar garantir que as proteções estão instaladas:
```bash
python scripts/install_hooks.py
```
*Isto irá copiar o arquivo `.githooks/pre-merge-commit` para o `.git/hooks/` do repositório local e garantir suas permissões de execução.*

## 2. Diagnóstico de Falha (Merge Bloqueado)
Se um merge falhou recentemente e o usuário pediu para checar o porquê:
```bash
# Ler as últimas 50 linhas do log de bloqueio
tail -n 50 .git/ci-merge-log.txt
```
*O Agente DEVE ler este log, informar o exato teste / lint que falhou, e oferecer uma correção imediata ANTES de tentar o merge de novo.*

## 3. Rodar o Gate Manualmente (Dry-Run)
Para validar o estado do repositório *sem* precisar fazer um merge:
```bash
python scripts/ci_gate.py
```
*Útil para o Agente validar que seu código está 100% livre de erros, lint limpo e tipagem coesa antes de confirmar alterações críticas.*
