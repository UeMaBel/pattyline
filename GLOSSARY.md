# Pattyline

Pattyline coordinates approved project tickets through implementation, verification, independent review, and integration.

## Language

**Valid ticket**:
A ticket whose required information is present and consistent. Validity alone does not authorize execution.
_Avoid_: Ready ticket

**Ready ticket**:
A ticket explicitly approved by a human for execution. Readiness does not imply that its dependencies are satisfied or that execution has started.
_Avoid_: Valid ticket

**Run**:
An execution of a fixed selection of human-approved tickets and their approved dependencies, captured at startup. A run may continue through dependency chains as their prerequisites become integrated.

**Implementation ticket**:
A ticket describing an approved change to a consuming project, eligible for Pattyline execution once its execution prerequisites are satisfied. Research, prototype, and grilling tickets belong to preparation instead.

**Integrated ticket**:
A ticket whose independently reviewed commit has been verified as included in the configured base branch. Review approval or a published branch alone does not establish integration.

**Preparation**:
The human-led process that turns an idea or existing work into a specification and explicitly approved implementation tickets. It includes clarification, domain modeling, ticket breakdown, and optional independent preparation review.

**Implementation review**:
An independent assessment of a ticket's implemented change and acceptance evidence, required before integration. It cannot be waived by skipping preparation review.

**Escalation**:
An approved handoff of local implementation work to a cloud worker, preserving the existing change, evidence, and remaining execution budget.

**Verification policy**:
The required checks declared by a ticket and the evidence needed to demonstrate its acceptance criteria. Workers may add evidence but cannot weaken approved verification requirements.

**Global setup**:
Per-user preparation of Pattyline's shared tools, coding-agent sign-in, and default preferences before any project is selected.

**Global settings**:
Per-user Pattyline preferences for default providers, models, and execution limits. They are independent of project configuration; a project may later adopt selected defaults.

**Coding agent**:
A tool that can work with source files and development commands on Pattyline's behalf. Codex is the first supported coding agent.
