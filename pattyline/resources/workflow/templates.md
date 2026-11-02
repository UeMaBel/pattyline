# Preparation response

Return one JSON object with session (actual fresh runtime session identity),
summary, decisions (array preserving settled choices), terms (term-to-definition
object), open_questions (array), artifacts (filename-to-Markdown object). Review
also returns verdict: approved, changes-required or blocked. Artifact paths are
stage-limited by Pattyline. A session result cannot approve executable tickets.

Breakdown also returns tickets: an array with number (immutable numeric string),
title, outcome, route (local/cloud), dependencies (native IDs), scope (relative
patterns), verification (name/phase objects), acceptance (one-line criteria array),
and cloud_escalation_allowed (boolean). Ask whether the proposed tickets match
the human's intent; Pattyline publishes them after an explicit yes.

# Glossary draft

`**Term**: concrete definition. Ambiguity/alternative: ...`

# ADR draft

`# Decision title`, context, decision, consequences and authoritative evidence.

# Specification

Purpose; actors; included/excluded scope; observable workflows; configuration;
failure behavior; approval/permissions; acceptance/evidence; external blockers.

# Ticket proposal

For each numbered slice: observable outcome, scope, acceptance criteria, explicit
blocking IDs, route, verification [{name, phase}], external evidence and approved
escalation. Include a dependency graph, ask whether these tickets match the
human's intent, and record any requested changes before publication.
