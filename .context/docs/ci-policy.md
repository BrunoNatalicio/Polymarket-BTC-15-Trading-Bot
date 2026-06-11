---
source: ci-policy.md
type: generic
---

---
trigger: always_on
---

# Dotcontext CI/CD Harness Engineering Policy

> Políticas estritas dos gates de CI/CD. DEVEM ser obedecidas por todos os agentes
> (Antigravity, Claude Code, OpenCode, Qwen Code, etc.).

## As 3 Regras Absolutas

1. **MANDATORY**: código e testes devem estar perfeitos ANTES de `git commit`/`git merge`.
   O hook roda a suíte de testes, lint e scanners. Falha = commit BLOQUEADO (exit 1).
2. **FORBIDDEN**: `--no-verify` é estritamente proibido em qualquer circunstância.
   Burlar o hook é violação da política de segurança do harness.
3. **PROCEDURE ON FAILURE**: não repetir às cegas. Ler o output do hook, entender a falha,
   corrigir o código e só então tentar de novo.

## Fail-Closed Environment

Se a venv não existir ou dependências faltarem, o gate bloqueia o commit explicitamente.

## Composição do gate neste repositório

- **Gate 0** — pin do Python: `.python-version` deve pinar `3.13`.
- **Gate 1** — dotcontext SSOT: `export-rules` (`.context/docs/rules-CLAUDE.md` → `CLAUDE.md`),
  `sync` e `reverse-sync`. O `.context/` é autoritativo.
- **Gate 2** — `uv run python .agent/scripts/checklist.py .`:
  - P0 Security Scan (`.agent/skills/vulnerability-scanner/`) — required; advisório no
    conteúdo (sempre exit 0 salvo erro de execução), bloqueia apenas em falha do scanner.
  - P1 Lint Check (`.agent/skills/lint-and-validate/`) — required; `ruff check .` +
    pyright ESCOPADO aos arquivos type-clean (bot.py tem ~111 erros pré-existentes;
    amplie a lista em `lint_runner.py` conforme os módulos forem limpos).
  - P3 Test Runner (`.agent/skills/testing-patterns/`) — required=True (NUNCA rebaixar
    para False); roda `test_tradingview_webhook.py` (hermético). NÃO usar pytest
    auto-discovery aqui: os demais scripts de teste batem em APIs reais.
  - P2/P4/P5 — ausentes neste repo; o checklist os trata como SKIP não-bloqueante.

## Caveats operacionais

- `.agent/` é kit vendorizado (origem: `C:\desenvolvendo\indicador\.agent`), versionado
  neste repo e excluído de ruff/pyright via `pyproject.toml`. Ao reinstalar/regenerar o
  kit, reconferir: flag `True` do Test Runner no `checklist.py`, lista de arquivos do
  pyright no `lint_runner.py`, lista de scripts herméticos no `test_runner.py`.
- Hook não dispara? Verificar `git config core.hooksPath` (= `.githooks`), permissão de
  execução, nome exato `pre-commit` sem extensão, line endings LF.
