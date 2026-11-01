# Pattyline

Pattyline is a Python CLI for human-approved preparation and sequential local/cloud ticket orchestration. This implementation is **not release-ready**: production execution remains gated on real runtime, platform and GitHub evidence.

Run from source with Python 3.11+: `py -3.12 -m pattyline --help` on Windows or `python3 -m pattyline --help` on Linux. Build/install offline with `python -m pip wheel --no-build-isolation --no-deps .` and `python -m pip install --no-index dist/pattyline-0.1.0-py3-none-any.whl` (pass `--wheel-dir dist` to the wheel command).

- [V1 specification](docs/pattyline/v1/spec.md)
- [Implementation tickets and verification catalog](docs/pattyline/v1/tickets.md)
- [Independent review findings](docs/pattyline/v1/review.md)
- [Proposed replacement ticket breakdown](docs/pattyline/v1/proposed-breakdown.md)
- [Runtime and skill-bundling research](docs/pattyline/v1/runtime-research.md)
- [Design interview decisions](pattyline-ideas.md)
- [Domain glossary](GLOSSARY.md)
- [Architectural decisions](docs/adr/)
- [Operator guide and implemented limits](docs/pattyline/v1/operator-guide.md)
- [Durable implementation progress](docs/pattyline/v1/implementation-progress.md)
- [Approved 22-slice implementation queue](docs/pattyline/implementation-v1/tickets.md)

The revised 22-slice breakdown and baseline/introduced-check policy were approved for this development session on 2026-11-01. Original 21 tickets are superseded drafts. Product implementation review remains mandatory. No personal skill installation is needed; bundled instructions/resources are Pattyline-owned and MIT-licensed.

Offline checks: `py -3.12 scripts/check.py all` and `py -3.12 scripts/check.py static`. Live release evidence: `py -3.12 scripts/check.py release-live` deliberately exits 2 while required evidence is missing. Ordinary tests use deterministic adapters; they cannot enable production execution.

`website_templates/` is the first consumer's reference snapshot. It is not an instruction to execute those tickets or deploy the website.
