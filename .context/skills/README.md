# Skills

On-demand expertise for AI agents. Skills are task-specific procedures that get activated when relevant.

> Project: developmentbot_polymarket

## How Skills Work

1. **Discovery**: AI agents discover available skills
2. **Matching**: When a task matches a skill's description, it's activated
3. **Execution**: The skill's instructions guide the AI's behavior

## Available Skills

### Built-in Skills

| Skill | Description | Phases |
|-------|-------------|--------|
| [Commit Message](./commit-message/SKILL.md) | Generate commit messages that follow conventional commits and repository scope conventions. Use when Creating git commits after code changes, Writing commit messages for staged changes, or Following conventional commit format for the project | E, C |
| [Pr Review](./pr-review/SKILL.md) | Review pull requests against team standards and best practices. Use when Reviewing a pull request before merge, Providing feedback on proposed changes, or Validating PR meets project standards | R, V |
| [Code Review](./code-review/SKILL.md) | Review code quality, patterns, and best practices. Use when Reviewing code changes for quality, Checking adherence to coding standards, or Identifying potential bugs or issues | R, V |
| [Test Generation](./test-generation/SKILL.md) | Generate comprehensive test cases for code. Use when Writing tests for new functionality, Adding tests for bug fixes (regression tests), or Improving test coverage for existing code | E, V |
| [Documentation](./documentation/SKILL.md) | Generate and update technical documentation. Use when Documenting new features or APIs, Updating docs for code changes, or Creating README or getting started guides | P, C |
| [Refactoring](./refactoring/SKILL.md) | Refactor code safely with a step-by-step approach. Use when Improving code structure without changing behavior, Reducing code duplication, or Simplifying complex logic | E |
| [Bug Investigation](./bug-investigation/SKILL.md) | Investigate bugs systematically and perform root cause analysis. Use when Investigating reported bugs, Diagnosing unexpected behavior, or Finding the root cause of issues | E, V |
| [Feature Breakdown](./feature-breakdown/SKILL.md) | Break down features into implementable tasks. Use when Planning new feature implementation, Breaking large tasks into smaller pieces, or Creating implementation roadmap | P |
| [Api Design](./api-design/SKILL.md) | Design RESTful APIs following best practices. Use when Designing new API endpoints, Restructuring existing APIs, or Planning API versioning strategy | P, R |
| [Security Audit](./security-audit/SKILL.md) | Review code and infrastructure for security weaknesses. Use when Reviewing code for security vulnerabilities, Assessing authentication/authorization, or Checking for OWASP top 10 issues | R, V |

## Creating Custom Skills

Create a new skill by adding a directory with a `SKILL.md` file:

```
.context/skills/
└── my-skill/
    ├── SKILL.md          # Required: source skill definition
    ├── scripts/          # Optional: deterministic helpers
    ├── references/       # Optional: load-on-demand details
    └── assets/           # Optional: output resources
```

### Skill Anatomy

```md
The source file under `.context/skills/` keeps internal scaffold metadata so dotcontext can track fill status.
When exported to AI-tool skill directories, the portable frontmatter should keep only:

---
name: my-skill
description: Describe what the skill does and the concrete triggers for when to use it
---

## Workflow
1. Step one
2. Step two

## Examples
```
[Short example]
```

## Quality Bar
- List the checks and constraints that keep the skill reliable

## Resource Strategy
- Explain when to add `scripts/`, `references/`, or `assets/`
```

Keep activation language in the description frontmatter, keep the body concise, and avoid extra docs such as `README.md` or `CHANGELOG.md` inside the skill folder.

## PREVC Phase Mapping

| Phase | Name | Skills |
|-------|------|--------|
| P | Planning | feature-breakdown, documentation, api-design |
| R | Review | pr-review, code-review, api-design, security-audit |
| E | Execution | commit-message, test-generation, refactoring, bug-investigation |
| V | Validation | pr-review, code-review, test-generation, security-audit |
| C | Confirmation | commit-message, documentation |
