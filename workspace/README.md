# workspace/

Target projects Saltcode operates on. Saltcode's own source lives at the repository
root; a project under `workspace/` is a *subject*, never part of the framework.

## Per-project convention (Task 0.5, REQ-MEM-002)

```
workspace/<project>/
└── .saltcode/                 # the Local Code-Wiki for this project
    ├── context_report.json    # Scout
    ├── design.md              # Architect (incl. ## HARD CONSTRAINTS)
    ├── tasks.json             # Planner
    ├── evaluator_report.json  # Evaluator
    ├── audit_result.json      # Auditor, per task
    ├── audit_log.jsonl        # every executed command (REQ-SEC-003)
    ├── tests/                 # Test-Intent specs, one task_{id}_spec.* per task
    ├── cache/                 # LanceDB: spec cache, semantic cache, notes, skills
    └── calibration/           # measured-then-fixed threshold artifacts (REQ-CAL-001)
```

`tests/` is tracked — the specs are contracts, written before code and immutable to
the Builder (REQ-TST-002). `cache/`, `calibration/`, and the log files are generated
and git-ignored.

After a successful sprint every artifact above exists and validates against its
pydantic schema (REQ-MEM-002 AC1).
