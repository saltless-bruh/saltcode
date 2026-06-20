---
name: saltcode-planning
description: Writes a structured implementation plan with small steps, exact files, and verification commands before executing any non-trivial task.
---

# Saltcode Planning Skill

Use this skill when preparing to implement any task or feature.

## 1. Rules
- Break down tasks into small chunks (2 to 10 minutes).
- Specify exact file paths to modify.
- Document verification commands (e.g. pytest commands) for each step.
- Detail rollbacks and risk mitigations.

## 2. Template
```markdown
### Goal
[Describe the feature or bug fix]

### Assumptions
[List preconditions or dependencies]

### Plan
1. [Step Name]
   - Files: `path/to/file`
   - Change: [Detail the edit]
   - Verify: [Exact command]

### Risks & Mitigations
- Risk: ... / Mitigation: ...

### Rollback Plan
- Commands to revert code modifications.
```
