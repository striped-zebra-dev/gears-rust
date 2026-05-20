# Common Rules (cyberware-sdlc)

## Navigation Rules

ALWAYS open and follow `{cypilot_path}/requirements/artifacts-registry.md` WHEN creating/registering artifacts in `artifacts.json`

## Artifact Chain

```
PRD → ADR → DESIGN → DECOMPOSITION → FEATURE → CODE
```

| From | To | Mechanism |
|------|----|-----------|
| PRD `fr`/`nfr` | DESIGN | **Required** coverage under `Architecture Drivers` |
| ADR `adr` | DESIGN | **Required** reference under `Architecture Drivers`; plus `**ADRs**: \`cpt-...\`` inline on principles/constraints |
| DESIGN `component` | DECOMPOSITION | **Required** coverage in feature entries |
| DECOMPOSITION `feature` | FEATURE | **Required** coverage under FEATURE H1 title |
| FEATURE `flow`/`algo`/`state`/`dod` | CODE | `to_code: true` — traced via `@cpt-*` markers |

## ID Format (REQUIRED)

All Cypilot IDs MUST:

- Use format: `cpt-{system}-{kind}-{slug}`
- Match regex: `^cpt-[a-z0-9][a-z0-9-]+$`
- Be wrapped in backticks: `` `cpt-...` ``
- Use only lowercase `a-z`, digits `0-9`, and `-` (kebab-case)

### ID Definition

When constraints require `task` and `priority` for an ID kind, ALWAYS use the checkbox form:

```markdown
- [ ] `p1` - **ID**: `cpt-{system}-{kind}-{slug}`
```

When constraints prohibit or only allow `task` and `priority`, use the plain form:

```markdown
**ID**: `cpt-{system}-{kind}-{slug}`
```

### ID Reference

Plain inline reference (any backticked `cpt-*` ID in text):

```markdown
`cpt-{system}-{kind}-{slug}`
```

Tracked reference with task checkbox and priority (used when constraints require `task`+`priority` on the reference):

```markdown
- [x] `p1` - `cpt-{system}-{kind}-{slug}`
```

### H1-Level IDs

Some IDs are placed directly under the document H1 title (before any H2):

- **DECOMPOSITION**: `status` ID (definition, checkbox form) — overall implementation status
- **FEATURE**: `featstatus` ID (definition, checkbox form) + `feature` ID (reference, checkbox form back to DECOMPOSITION)

### ADR YAML Frontmatter

ADR files MUST start with YAML frontmatter:

```yaml
---
status: {proposed | accepted | deprecated | superseded}
date: {YYYY-MM-DD}
---
```

## Template and Example Paths

| Artifact | Template | Examples |
|----------|----------|----------|
| PRD | `docs/spec-templates/cyberware-sdlc/PRD/template.md` | — |
| ADR | `docs/spec-templates/cyberware-sdlc/ADR/template.md` | `docs/spec-templates/cyberware-sdlc/ADR/examples/*.md` |
| DESIGN | `docs/spec-templates/cyberware-sdlc/DESIGN/template.md` | — |
| DECOMPOSITION | `docs/spec-templates/cyberware-sdlc/DECOMPOSITION/template.md` | `docs/spec-templates/cyberware-sdlc/DECOMPOSITION/examples/example.md` |
| FEATURE | `docs/spec-templates/cyberware-sdlc/FEATURE/template.md` | `docs/spec-templates/cyberware-sdlc/FEATURE/examples/*.md` |
