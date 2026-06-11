# Setup — Maestro Harness Pre-Commit Gate com dotcontext

> **Documento de provisionamento para agente de IA (Antigravity, Claude Code, Cursor, OpenCode, Qwen Code, etc.).**
> Objetivo: replicar em um NOVO repositório o mesmo gate de pre-commit fail-closed usado no repo
> `indicador` (Al Brooks RAG Bot): hook Git → dotcontext sync (SSOT `.context/`) → checklist de
> auditoria (lint, security, testes) → commit só passa se tudo estiver verde.
>
> Repo de origem (referência canônica): `C:\desenvolvendo\indicador`

---

## 0. Visão geral da arquitetura

```
git commit
   └─► .githooks/pre-commit  (bash, ativado via core.hooksPath)
         ├─ [gate 0] Pin de versão do Python (.python-version)        ← adaptável
         ├─ [gate 1] dotcontext SSOT:
         │     ├─ npx @dotcontext/cli export-rules --preset claude --force
         │     │     (.context/docs → CLAUDE.md; re-stage automático)
         │     ├─ npx @dotcontext/cli sync --target .context
         │     │     (.context/agents → diretórios das IDEs de IA)
         │     └─ npx @dotcontext/cli reverse-sync
         │           (edits feitos nas IDEs → de volta ao .context/)
         └─ [gate 2] uv run python .agent/scripts/checklist.py .
               ├─ P0 Security Scan      (required → bloqueia)
               ├─ P1 Lint Check         (required → bloqueia)
               ├─ P2 Schema Validation  (optional → WARN)
               ├─ P3 Test Runner        (required → bloqueia)  ← ver §6
               ├─ P4 UX Audit           (optional → WARN)
               └─ P5 SEO Check          (optional → WARN)
   exit != 0 em QUALQUER required → commit ABORTADO (fail-closed)
```

Princípio **fail-closed**: integridade > conveniência. Se algo não pode ser verificado
(venv ausente, dependência faltando, script quebrado), o commit é bloqueado — nunca liberado
"na dúvida".

---

## 1. Pré-requisitos na máquina

| Item | Como verificar | Como instalar |
|---|---|---|
| Node.js ≥ 18 + npm | `node --version` | nodejs.org |
| `@dotcontext/cli` (global) | `npx @dotcontext/cli --version` → `0.9.2` | `npm install -g @dotcontext/cli` |
| Git Bash (Windows) | hooks `.githooks/*` são bash; o Git for Windows já executa | git-scm.com |
| `uv` (se repo Python) | `uv --version` | `pip install uv` ou instalador oficial |
| Python pinado (se repo Python) | `cat .python-version` | `uv python install <ver>` |

> O `@dotcontext/cli` é instalado **globalmente via npm** (não como dependência do repo).
> No repo de origem ele está em `%APPDATA%\npm`.

---

## 2. Estrutura de arquivos a criar no novo repositório

```
<novo-repo>/
├── .githooks/
│   └── pre-commit                  ← hook principal (template no §4)
├── .context/
│   └── docs/
│       ├── rules-CLAUDE.md         ← regras do projeto (vira CLAUDE.md via export-rules)
│       └── ci-policy.md            ← política do gate (template no §5)
├── .agent/
│   └── scripts/
│       └── checklist.py            ← orquestrador de auditoria (copiar do repo de origem)
│   └── skills/<skill>/scripts/*.py ← scanners chamados pelo checklist (copiar junto)
├── .python-version                 ← pin (ex.: "3.12") — só para repos Python
└── CLAUDE.md                       ← GERADO pelo export-rules; não editar como fonte primária
```

**Origem dos arquivos `.agent/`:** o kit Antigravity completo vive em
`C:\desenvolvendo\indicador\.agent\`. Copiar no mínimo:

- `.agent/scripts/checklist.py`
- `.agent/skills/vulnerability-scanner/scripts/security_scan.py`
- `.agent/skills/lint-and-validate/scripts/lint_runner.py`
- `.agent/skills/testing-patterns/scripts/test_runner.py`
- (opcionais, viram SKIP se ausentes: `schema_validator.py`, `ux_audit.py`, `seo_checker.py`)

O `checklist.py` trata script ausente como **SKIP não-bloqueante**, então o conjunto mínimo
funciona; adicione scanners conforme o stack do novo repo.

> ⚠️ **Decisão de versionamento:** no repo de origem, `.agent/` é **não-rastreado**
> (`.git/info/exclude`) — isso já causou perda de configuração ao reinstalar o kit.
> Recomendação para o novo repo: **versionar `.agent/scripts/` e os scanners usados pelo gate**,
> ou no mínimo documentar o caveat do §6.

---

## 3. Passo a passo de instalação

Execute na raiz do novo repositório (Git Bash ou PowerShell, conforme indicado):

```bash
# 1. Garantir o CLI global
npm install -g @dotcontext/cli

# 2. Criar a estrutura SSOT
mkdir -p .context/docs .githooks

# 3. Criar .context/docs/rules-CLAUDE.md com as regras do projeto
#    (frontmatter obrigatório — ver template no §5.1)

# 4. Criar .context/docs/ci-policy.md (template no §5.2)

# 5. Copiar o kit .agent/ do repo de origem (mínimo: scripts/checklist.py + scanners)
#    Windows: robocopy C:\desenvolvendo\indicador\.agent .agent /E

# 6. Criar .githooks/pre-commit (template no §4) e torná-lo executável
chmod +x .githooks/pre-commit

# 7. ATIVAR o hook — este é o passo que liga tudo:
git config core.hooksPath .githooks

# 8. Gerar o CLAUDE.md inicial a partir do .context
npx @dotcontext/cli export-rules --preset claude --force

# 9. Validar manualmente o checklist antes do primeiro commit
uv run python .agent/scripts/checklist.py .
#    (repo não-Python: python .agent/scripts/checklist.py .)

# 10. Smoke test do gate completo
git add -A && git commit -m "chore: instala Maestro Harness pre-commit gate"
#    Deve imprimir "MAESTRO HARNESS PRE-COMMIT GATE" e as etapas [1/3]..[2/3]
```

### Verificação pós-instalação

```bash
git config core.hooksPath        # deve imprimir: .githooks
npx @dotcontext/cli --version    # deve imprimir: 0.9.2 (ou superior)
```

---

## 4. Template do hook — `.githooks/pre-commit`

Adaptar os blocos marcados com `# ADAPTÁVEL`. O shebang bash funciona no Windows porque o
Git for Windows executa hooks via Git Bash.

```bash
#!/usr/bin/env bash

# ADAPTÁVEL (repos Python): ativa a venv para que subprocessos (pytest, mypy, ruff)
# usem o Python do projeto. Em Linux/macOS o caminho é .venv/bin
export PATH="$(pwd)/.venv/Scripts:$PATH"
export PYTHONUTF8=1

echo "========================================="
echo "MAESTRO HARNESS PRE-COMMIT GATE"
echo "========================================="

# ADAPTÁVEL: gate de versão do Python — remova o bloco se o repo não for Python,
# ou troque "3.12" pela versão pinada do novo projeto
PINNED_VER=$(cat .python-version 2>/dev/null | tr -d '[:space:]' | cut -d. -f1-2)
if [ "$PINNED_VER" != "3.12" ]; then
  echo "ERRO: .python-version deve pinar 3.12 (encontrado: '$PINNED_VER')."
  exit 1
fi
echo "Python pin: 3.12 OK"

echo "[1/3] Syncing Dotcontext SSOT..."
# Exporta .context/docs → CLAUDE.md (edits no .context propagam para as IDEs de IA)
npx @dotcontext/cli export-rules --preset claude --force
if [ $? -ne 0 ]; then
  echo "ERRO: dotcontext export-rules falhou!"
  exit 1
fi
# Re-stage do CLAUDE.md caso o export-rules o tenha atualizado
git add CLAUDE.md 2>/dev/null || true

# Sincroniza agentes de .context/agents → diretórios das IDEs de IA
npx @dotcontext/cli sync --target .context
if [ $? -ne 0 ]; then
  echo "ERRO: dotcontext sync falhou!"
  exit 1
fi

# Reverse-sync: importa edits feitos pelas IDEs de volta ao .context (o .context é autoritativo)
npx @dotcontext/cli reverse-sync
if [ $? -ne 0 ]; then
  echo "ERRO: dotcontext reverse-sync falhou!"
  exit 1
fi

echo "[2/3] Running Complete Audit (Lint, Security, Tests)..."
# ADAPTÁVEL: repo não-Python pode trocar "uv run python" por "python" ou outro runner
uv run python .agent/scripts/checklist.py .
if [ $? -ne 0 ]; then
  echo "ERRO: Checklist falhou! Commit abortado para proteger a branch."
  echo "Corrija os erros listados acima antes de commitar novamente."
  exit 1
fi

echo "OK: All checks passed! Maestro Harness is clear. Commit allowed."
exit 0
```

> **Nota Windows:** evite emoji em `echo` dentro do hook — o console cp1252 pode quebrar.
> No Python isso é resolvido com `PYTHONUTF8=1` (já exportado acima) e o
> `sys.stdout.reconfigure(encoding="utf-8")` que o `checklist.py` já faz.

---

## 5. Templates do `.context/docs/`

### 5.1 `rules-CLAUDE.md` (vira o CLAUDE.md das IDEs)

O frontmatter duplo é exigido pelo dotcontext (o primeiro bloco identifica a fonte; o segundo
é a diretiva de trigger consumida pelas IDEs):

```markdown
---
source: CLAUDE.md
type: generic
---

---
trigger: always_on
---

# CLAUDE.md - Rules & Base Context

## CI/CD Merge Gate Protocol (CRITICAL)

**MANDATORY**: Este repositório usa um hook Git pre-commit fail-closed (Maestro Harness
em `.githooks/pre-commit`). Siga `.context/docs/ci-policy.md`.
1. NUNCA usar `git commit --no-verify` ou `git merge --no-verify`. Absolutamente proibido.
2. Todo `git commit` roda: dotcontext export-rules + sync + reverse-sync e
   `python .agent/scripts/checklist.py .`. O commit ABORTA se houver erro de lint,
   security, teste ou contexto.
3. Se o commit falhar, foi porque o código quebrou. NÃO repita às cegas — leia o output
   do hook, corrija e tente de novo.
4. Para reativar o hook: `git config core.hooksPath .githooks`.

## [Diretivas específicas do novo projeto aqui]
```

### 5.2 `ci-policy.md`

```markdown
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
```

Depois de criar/editar arquivos em `.context/docs/`, o próprio hook propaga para `CLAUDE.md`
no próximo commit (ou rode `npx @dotcontext/cli export-rules --preset claude --force` à mão).

**Fluxo bidirecional do dotcontext:**
- Editar `.context/docs/rules-CLAUDE.md` → commit → `export-rules` empurra para `CLAUDE.md`.
- Editar `CLAUDE.md` diretamente → commit → `reverse-sync` puxa de volta para `.context/`.

---

## 6. Caveats críticos (aprendidos no repo de origem)

1. **Test Runner DEVE ser `required=True` no `checklist.py`.**
   Em `CORE_CHECKS`, a tupla é `("Test Runner", ".agent/skills/testing-patterns/scripts/test_runner.py", True)`.
   O kit Antigravity original vem com `False` (falha de teste vira WARN não-bloqueante) —
   isso quebra a Regra 1. Se reinstalar/regenerar o kit `.agent`, **verifique este flag**.

2. **Timeouts encadeados.** O `checklist.py` envolve cada scanner com timeout próprio
   (origem: 1200s) e o `test_runner.py` tem timeout interno (origem: 1140s — o interno deve
   ser MENOR que o wrapper para falhar com mensagem clara em vez de ser morto silenciosamente).
   Suítes lentas com timeout default de 300s geram falso FAIL intermitente.

3. **Diagnóstico de falha de teste.** O `checklist.py` da origem imprime o tail do **stdout**
   do pytest em caso de falha (o stderr vem vazio — sem isso a falha é indecifrável no log
   do hook). Preserve esse comportamento ao copiar.

4. **`test_runner.py` deve usar o runner do projeto.** Na origem ele roda
   `uv run python -m pytest -q --tb=no` com auto-descoberta de `pytest.ini`. Adapte ao
   stack do novo repo (npm test, cargo test, etc.).

5. **Hook não dispara?** Checar nesta ordem:
   `git config core.hooksPath` (deve ser `.githooks`) → arquivo é executável
   (`chmod +x`) → nome exato `pre-commit` sem extensão → line endings LF (não CRLF).

6. **`npx` precisa do Node no PATH do Git Bash.** Se o hook falhar com "npx: command not
   found", o Node não está visível no ambiente bash — reinstale o Node com a opção de PATH
   ou adicione manualmente.

---

## 7. (Opcional) Gate de merge — `pre-merge-commit`

O repo de origem tem um segundo hook para merges, instalado por `scripts/install_hooks.py`
(copia `.githooks/pre-merge-commit` para `.git/hooks/` e dá permissão de execução).
Ele delega para `scripts/ci_gate.py`, que grava o diagnóstico em `.git/ci-merge-log.txt`.
Replique apenas se o novo repo fizer merges locais gateados; para a maioria dos casos o
`pre-commit` via `core.hooksPath` já cobre (commits de merge também passam por ele).

---

## 8. Checklist final de aceitação

- [ ] `git config core.hooksPath` retorna `.githooks`
- [ ] `npx @dotcontext/cli --version` funciona no Git Bash
- [ ] `.context/docs/rules-CLAUDE.md` e `ci-policy.md` existem com frontmatter duplo
- [ ] `export-rules --preset claude --force` gera/atualiza `CLAUDE.md`
- [ ] `python .agent/scripts/checklist.py .` passa (ou só WARNs opcionais)
- [ ] `("Test Runner", ..., True)` confirmado no `checklist.py`
- [ ] Commit de teste imprime o banner do Maestro e completa
- [ ] Commit com teste quebrado de propósito é ABORTADO (validar o fail-closed!)
- [ ] Regra "no `--no-verify`" registrada no CLAUDE.md/regras da IDE
