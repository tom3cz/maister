# Autonomous Mode

## TL;DR
The procedure for runs started with `--autonomous` / `--no-questions`; trigger, scope and the never-inferred rule live in orchestrator-patterns.md § 2.2.
Every ask site resolves in one order: hard stop > explicit flag > "(Recommended)" > this file's site table > hard stop. Nothing is guessed.
Rollback, destructive operations, manual completion and commits are never auto-selected. Real blockers stop after 3 genuine attempts and ask the decision plus whether to keep the mode on.
Every auto-answer is one `auto_decisions` entry in state; phase summaries, dashboard, work-log and the final "Autonomous decisions" block are views of it.
The site tables give each ask site's id, location, default and budget.

## Key Decisions
- Single source: § 2.2 holds only trigger, scope, never-inferred and a pointer; consumer files carry one state-keyed clause per site and point here.
- Budget: at least 3 genuine attempts per site; "Keep autonomous mode on" at a budget hard stop buys one fresh budget, logged. In Phase 11 the retry answer buys it instead.
- Safety-critical Phase 2 picks are auto-taken only when the gap-analyzer gives a `recommendation`, flagged `safety_critical: true` and listed first in the final block.
- Quick skills have no state file: they count attempts in the conversation and fall back to asking after compaction.

## Open Questions / Risks
- Claude Code tool-permission prompts are outside the plugin; an unattended run should pair the flag with the `auto` or `acceptEdits` permission mode.
- Safety-critical picks get no human review before code is written; the flagged list in the final block is the only mitigation.
- Token ambiguity is a model judgment. It fails safe: the run stays interactive.

## How to Read This File

Read it only while the mode is active: `orchestrator.options.autonomous` is `true` in orchestrator-state.yml, or (quick skills) this run's Step 1 found the token. Read it before the first ask site and again after every compaction or resume while that holds. Interactive runs never load it.

| Consumer | Reads |
|---|---|
| development orchestrator | all `Shared:` sections + `## Development` |
| implementation-plan-executor | all `Shared:` sections + `## Executor` |
| implementation-verifier | `## Verifier` |
| quick-dev / quick-bugfix | all `Shared:` sections + `## Quick-dev` / `## Quick-bugfix` |
| `/maister:work` and task-classifier | `## Router & classifier` |

Site ids (`dev.P6.revise`, `exec.group-failure`, …) are stable: consumer files and `auto_decisions[].site` refer to them. Line numbers in the tables are approximate. Numbered references (§ 2.2, § 4, § 8) point to orchestrator-patterns.md; named ones (§ Shared: Hard Stops) point to this file.

---

## Shared: Resolution

**Precedence at every ask site** — take the first that applies:
1. **Hard-stop condition** (§ Shared: Hard Stops) → a real AskUserQuestion. This always wins.
2. **Explicit command flag** (`--audit`/`--no-audit`, `--e2e`/`--no-e2e`, `--user-docs`/`--no-user-docs`, `--sequential`, `--from`) → follow it; basis `flag`.
3. **The "(Recommended)" option** → take it; basis `recommended`. A site row may add a budget or a follow-up, never a different pick.
4. **The site-table entry** → apply it; basis `default`.
5. **None of these** → hard stop. Never guess.

**Model-authored questions** (clarifications, technical questions, requirements): draft them as usual, then answer each from the best evidence — codebase analysis, research context, gap-analyzer recommendation, existing patterns, least change. Record each as an assumption (basis `assumption`) and mark it `Assumed (autonomous): …` with its evidence in the artifact that would have held the answer.

**Launch-time asks still ask** — they are not gates: quick-dev with no task; quick-bugfix with no description and no bug context in the session; `/maister:work` with no argument; the `/maister:work` resume menu; the classification-failed fallback.

**Gate presentation**: no executive summary. Read only the artifact's § 7 TL;DR, print one line `Auto: <site> → <chosen>`, and do a single dashboard rewrite (patterns § 8 moments 3 and 5 merge into it).

**State ordering**: finish the phase work → resolve the gate → write the `auto_decisions` entry and add the phase to `completed_phases` in the same state write. This takes the place of "user responds" in the patterns § 2 ordering rule without adding a write.

**Phase-entry self-checks**: the preceding gate's `auto_decisions` entry stands in for the AskUserQuestion call; if neither exists, resolve that gate now before the phase runs.

**Re-read** this file after every compaction or resume while the state key is `true`; the state file, not memory, says whether the mode is on (quick skills have none — see `qdev.compaction`).

---

## Shared: Hard Stops

**Never auto-selected, at any site** (the user-confirmed rollback principle — a human picks these):
- rollback / revert, or any destructive file or git operation
- "Complete manually"
- "Accept with justification"
- "Skip TDD with doc"
- "Skip fixes, proceed as-is" while criticals are present
- "Finalize without it" for a required phase (any phase but 12/13; 12/13 too when an explicit `--e2e` / `--user-docs` flag enabled it)
- commit, push or PR — Phase 14 only prints the commit template

**Immediate hard stops** (nothing to retry, no counter):
- a malformed state file (patterns § 4 Write Rule) — `fw.state.malformed`
- missing resume prerequisites (patterns § 5) — `fw.resume.prereq`
- missing executor or verifier prerequisites — `exec.locate`, `dev.verify-prereq`
- a Phase 2 decision with no `recommendation` or with `recommendation: null` (for an important decision: no `recommendation` and no `default`)
- any ask site with no "(Recommended)" option and no site-table entry
- critical E2E failures in a Phase 12 that an explicit `--e2e` flag enabled — no code is edited after Phase 11 (`dev.P12.exit`)

**Budget hard stops** (after 3 genuine attempts; for Phase 11 also as soon as a round has nothing fixable left while criticals remain):
- the Phase 11 fix loop: criticals still open, or a non-empty `carried_criticals` set (whatever status the latest report shows), after its 3rd counted fix round, or in a round with nothing fixable left to change (that round stops at once, without re-verifying unchanged code) — `dev.P11.max`
- a Phase 12 / 13 that an explicit `--e2e` / `--user-docs` flag enabled and that still could not run after 3 genuine attempts — `dev.P12.base-url`, `dev.P13.base-url`
- an executor group failure; an executor test step still missing; an unclear executor test failure
- a TDD red test that won't fail (Phase 3, quick-bugfix Step 5); a TDD green test still failing (Phase 9)
- a spec revision that still meets the Phase 6 trigger
- a Phase 14 re-run of a required phase that is still unresolved
- any Auto-Recovery row whose last step is "prompt user" / "ask user" (§ Shared: Retry-Budget Override)

**Genuine attempt**: diagnose → change something (a re-dispatch, a fix, a revision) → re-run the failing check, and it still fails. A re-run without a change does not count.
- Counters live in `orchestrator.autonomous_attempts`, keyed `<site>` or `<site>.<group>` (e.g. `exec.group-failure.g3`).
- Phase 11 counts rounds instead: a round counts only when it applied at least one fix, and then adds 1 to `verification_context.reverify_count` (the re-verify history, never reset) and to the `dev.P11.max` entry of `autonomous_attempts` (the budget). A round with nothing fixable left to change is not re-verified and ends the loop (`dev.P11.max`).
- Quick skills have no state; they count in the conversation.

**Hard-stop procedure**:
1. Save state: the phase stays `in_progress`; attempts so far are recorded.
2. Unless `options.html_output` is false (patterns § 8 config gate), rewrite the dashboard with `current_activity` = "Hard stop — waiting for your answer: <reason>".
3. Ask (a) for every unresolved decision, then (b) once. Per AskUserQuestion call: at most 3 decision questions plus (b); each critical decision gets its own call; with several calls, (b) goes once, in the last call. Where the tool cannot take two questions in one call, ask them one after the other.
   - (a) The site's usual question and options, with no option pre-marked as the autonomous pick, prefaced by what was tried (attempt count, last failure). Rollback stays on offer — a human is answering. For `exec.group-failure` use exactly four options: "Retry group (applies the suggested fix if one was identified)", "Complete manually", "Rollback changes", "Stop". The executor's interactive prompt is unchanged. Where a site row names the (a) options (`dev.P11.max`, `dev.P12.exit`, `dev.P12.base-url` / `dev.P13.base-url`), use those.
   - (b) "Keep autonomous mode on after this?" with "Keep autonomous mode on" / "Continue interactively (turn autonomous mode off)".
4. Log both answers as one `auto_decisions` entry with basis `hard-stop`, then act on them:
   - "Continue interactively" → set `options.autonomous: false`; the next dashboard rewrite drops `task.mode`; later gates ask.
   - "Keep autonomous mode on" at a budget hard stop → reset that site's counter to 0 (its `autonomous_attempts` entry) for one fresh 3-attempt budget, and say so in `reason`. Phase 11 is the exception (next item); there only the `dev.P11.max` entry is reset, never `reverify_count`, which keeps the re-verify history. An immediate hard stop has no counter to reset.
   - Phase 11 (`dev.P11.max`): the retry answer (a), "Try up to 3 more fix rounds" or "I fixed it — re-verify", grants the fresh budget whatever (b) is chosen; (b) only sets the mode. With "Continue interactively" after a retry grant, write `verification_context.reverify_baseline` = the current `reverify_count` (after the "I fixed it" re-verify, when that was the answer); the interactive loop counts its max 3 iterations from it.
5. For a malformed state file, write the mode change only after the repair has been validated.

---

## Shared: Retry-Budget Override

While the state key is `true`, development's Auto-Recovery table (development SKILL.md § Auto-Recovery, ~L659-670) is read with two overrides. The table itself is not edited.
- Every row gets at least 3 genuine attempts: the 2s become 3, rows at 3 stay 3, and Phase 8's 5 stays 5.
- A final "prompt user" / "ask user" step (Phases 1 and 2) becomes a budget hard stop. Phase 3's "skip TDD with doc" is never taken; that row ends in a budget hard stop too.

---

## Shared: State & Audit Trail

All writes follow the patterns § 4 Write Rule (key by key, re-read after every write) and Timestamp Rule.

| Key | Shape | Rule |
|---|---|---|
| `orchestrator.options.autonomous` | `true` \| `false` | Development Domain Context key; missing = `false`. Written explicitly at init. Read by development, the executor (Phase 2 entry) and the verifier (Step 1). |
| `orchestrator.autonomous_attempts` | map: site id → attempts | Created when the key first becomes `true`; kept afterwards, also after "Continue interactively" or a manual `false`. Separate from the phase-keyed `auto_fix_attempts`. |
| `orchestrator.auto_decisions[]` | list of entries | A sibling of `options`. Created when the key first becomes `true`; kept afterwards (append-only), also after "Continue interactively" or a manual `false`. The single record of every auto-answer and hard stop. |
| `verification_context.carried_criticals` | list: one short id + location per critical | Missing = empty. When the key turns `true` during Phase 11 (a resume with the token), seed it from the latest verification report's criticals. Written only while the key is `true`, entry by entry under the § 4 Write Rule. Every critical a Phase 11 verify run reports is added; an item leaves only through a logged fix (`fixes_applied`), never because a re-verify stopped reporting it. Read by `dev.P11.fix` / `dev.P11.max`. |
| `verification_context.reverify_baseline` | integer | Written at a Phase 11 hard stop that grants more rounds and turns the mode off ("Continue interactively"): the `reverify_count` at that moment. Development's interactive loop counts its max 3 iterations from it. Missing = 0. |

**Entry fields** (`auto_decisions[]`):

| Field | Meaning |
|---|---|
| `at` | Full ISO 8601 UTC from `date -u` (§ 4 Timestamp Rule) |
| `phase` | `phase-1` … `phase-14`; executor entries use `phase-8`, verifier entries `phase-11` |
| `site` | Site id from the tables below |
| `question` | Short form of the question that would have been asked |
| `chosen` | Option label taken, or the assumption text |
| `basis` | `recommended` \| `default` \| `assumption` \| `flag` \| `hard-stop` |
| `reason` | One line: evidence or rule applied, attempt count, budget reset |
| `safety_critical` | Optional; `true` only for auto-picked `decisions_needed` critical items |

**Surfaces** — one record, derived views; no surface adds a write of its own:

| Surface | Written when | Content |
|---|---|---|
| `orchestrator.auto_decisions[]` | The state write that resolves the gate | The full entry |
| `phase_summaries.[phase].decisions` | The existing context-extraction write | `{decision: "<site>: <chosen> — auto", rationale: <reason>}` |
| `dashboard-data.js` | The existing gate-moment rewrite | `gate.answer` = `"<chosen> — auto"` (hard-stop answers get no suffix); `task.mode: "autonomous"` while the key is `true`, omitted otherwise |
| `implementation/work-log.md` | From Phase 8 on (the executor writes its own lines; the orchestrator writes Phases 9-14) | One line per auto-answer |
| `clarifications.md`, `scope-clarifications.md`, `technical-clarifications.md`, `requirements.md` | The Phase 1 / 2 / 5 artifact writes | `Assumed (autonomous): …` / `Decided (autonomous): …` lines with evidence |
| Final "Autonomous decisions" block | Phase 14, derived from `auto_decisions[]`; quick skills build it from the conversation | See below |

**Final "Autonomous decisions" block**, in this order:
1. "Safety-critical decisions (review these)" — every entry with `safety_critical: true`
2. Remaining entries grouped by phase, one line each (`site → chosen — reason`)
3. Assumptions, each with a pointer to the file that records it
4. Hard stops and budget resets
5. Warnings proceeded with (e.g. Phase 11 known issues; Phase 12 E2E failures, with their counts and the report path)
6. Phase 4 mockup paths
7. Optional phases converted to skipped, with their `skip_reason`

---

## Development

Consumer: `skills/development/SKILL.md`. Every inline clause there is keyed on `orchestrator.options.autonomous` being `true`. In the shared self-check clause, "the preceding gate" for Phase 11 means the Phase 10 Q1–Q3 entries (`dev.P10.q1`-`q3`); for every other phase it is the previous phase's exit gate.

| Site | Location (~line) | Default | Budget / stop |
|---|---|---|---|
| `dev.init.token` | Step 0 (~L25), Step 3 item 4 (~L50) | Apply the § 2.2 token rule. Write `options.autonomous` explicitly: `true` when the token is present or the key is already `true`, else `false`. Only when `true`: write `auto_decisions: []` and `autonomous_attempts: {}` under `orchestrator:` only if absent — never reset either on resume (the log is append-only; a budget resets only through the hard-stop "Keep autonomous mode on" answer); seed `task.mode`; print "Mode: autonomous — gates are auto-answered and logged; hard stops still ask", on every start and resume, adding " (from state)" when the key was already `true`. On resume a token sets the key `true`; without one the state value stands. To resume interactively, the user sets `orchestrator.options.autonomous: false` in the state file before resuming. | — |
| `dev.P1.clarify` | Phase 1 step 3 (~L137) | Draft ≤5 questions; answer each from evidence; write `Q / Assumed (autonomous): … (evidence: …)` to `analysis/clarifications.md` | — |
| `dev.P2.decision` | Phase 2 Decision Gate (~L158-164) | Critical → `recommendation`, logged `safety_critical: true` (a safety-critical item that carries a recommendation is still auto-picked and flagged). Important → `recommendation`, else `default`. Record `Decided (autonomous): …` in `scope-clarifications.md`. | Any item missing its pick, or carrying `recommendation: null` (the gap-analyzer withholds a pick only for a safety-critical item it lacks evidence for) → a hard stop on that decision (§ Shared: Hard Stops step 3: each critical decision in its own call, at most 3 important decisions per call, the keep-mode question once in the last call) |
| `dev.P2.route` | Phase 2 exit gate (~L178-193) | Continue to the computed next phase (3, 4 or 5) | — |
| `dev.P3.red` | Phase 3 (~L201-208) | The test must fail; rewrite and re-run until it does | 3 genuine attempts → hard stop. Never "Skip TDD with doc". |
| `dev.P3.exit` | Phase 3 gate (~L210-212) | Continue | — |
| `dev.P4.exit` | Phase 4 gate (~L241-243) | Approve the single pass, then shut the companion down (`POST /shutdown`). List mockup paths in the final block. | — |
| `dev.P5.A.tech` | Phase 5 step A1 (~L257) | Technical questions become assumptions in `technical-clarifications.md` | — |
| `dev.P5.A.arch` | Phase 5 step A2 (~L258) | Approach ranked first by evidence: research `recommended_approach` > gap-analysis recommendation > fit with existing patterns > least change. Log the rejected alternatives. | — |
| `dev.P5.B.reqs` | Phase 5 step B (~L264-278) | "I assume X" items stay assumed; the three REQUIRED questions are answered from evidence; visual assets = only what Step 4 ingested. Mark them `Assumed (autonomous)` in `requirements.md`. | — |
| `dev.P5.exit` | Phase 5 gate (~L293-295) | Continue (to Phase 6 unless `--no-audit`) | — |
| `dev.P6.run` | Phase 6 (~L310) | "Yes, run audit (Recommended)" unless `--no-audit` | — |
| `dev.P6.revise` | Phase 6 gate (~L312-314) | Trigger: verdict Non-Compliant, OR verdict `fail`, OR any Critical finding. Then re-invoke specification-creator with the audit findings to revise `spec.md`/`spec.html` in place and re-run spec-auditor. No trigger → continue. | ≤3 revisions → hard stop |
| `dev.P6.exit` | Phase 6 gate (~L314) | Continue | — |
| `dev.P7.exit` | Phase 7 gate (~L334-336) | Continue in the same context — no `/clear` handoff; the executor re-reads the plan from disk | — |
| `dev.P8.exit` | Phase 8 gate (~L363-365) | Continue | — |
| `dev.P9.green` | Phase 9 (~L373-380) | The test must pass; return to implementation and fix | 3 genuine attempts → hard stop |
| `dev.P9.exit` | Phase 9 gate (~L384) | Continue | — |
| `dev.P10.q1` | Phase 10 Q1 (~L418-419) | All four reviews `true`; production readiness runs at target `production` | — |
| `dev.P10.q2` / `dev.P10.q3` | Phase 10 Q2/Q3 (~L421-423) | `true` whenever the question would be asked; `--no-e2e` / `--no-user-docs` win | — |
| `dev.P11.fix` | Phase 11 Step 4 (~L460-467) | "Fix all fixable issues": each round attempts every critical and warning that the patterns § 6 Fixability Assessment marks fixable; info is ignored; then set `skip_test_suite: false`. Non-fixable criticals are carried forward in `verification_context.carried_criticals` — never auto-attempted, never auto-accepted. Every critical a verify run reports joins that set; it leaves only through a logged fix (`fixes_applied`), never because a re-verify stopped reporting it. | Only rounds that apply at least one fix count: `reverify_count` (history) and `autonomous_attempts` `dev.P11.max` (budget) |
| `dev.P11.rerun` | Phase 11 Step 4 (~L468-470) | "Yes, re-run verification" after a round that applied at least one fix; a round with nothing fixable left is never re-verified (`dev.P11.max`) | — |
| `dev.P11.max` | Phase 11 exit conditions (~L473-477) | Each round: fix the fixable issues, carry non-fixable criticals forward, re-verify. Proceed only when the latest report has no critical AND `carried_criticals` is empty, whatever the reported status: a `passed` report with a non-empty set is a round with the carried criticals still open. Warnings only → proceed, logged as known issues. A round with nothing fixable left to change ends the loop at once: no critical and an empty set → proceed; otherwise the budget hard stop below (an idle stop), now, without re-verifying unchanged code. | Criticals or a non-empty `carried_criticals` after the 3rd counted round, or in a round with nothing fixable left → budget hard stop. After the 3rd round, while the latest report still has fixable issues, the (a) options are "Try up to 3 more fix rounds" / "Proceed with known issues" / "Stop workflow". At an idle stop the (a) options are "I fixed it — re-verify" / "Proceed with known issues" / "Stop workflow". "I fixed it — re-verify": log the user's fix in `fixes_applied`, clear the carried entries it names, re-verify once (it adds 1 to `reverify_count`, not to the budget), then continue the loop with a fresh budget. The retry answer grants the budget, whatever (b) is chosen: it resets the `dev.P11.max` entry of `autonomous_attempts`, never `reverify_count`; with "Continue interactively" it also writes `reverify_baseline` (§ Shared: Hard Stops step 4). |
| `dev.P11.exit` | Phase 11 gate (~L485-487) | Continue | — |
| `dev.P12.base-url` | Phase 12 prompt (~L499) | Infer `base_url` from evidence (logged assumption). Unreachable → start the app, recording every process started. The verifier could not run at all (app still unreachable, or Playwright MCP / browser unavailable) → retry with a change (start the app, re-dispatch); after 3 genuine attempts → `e2e_enabled: false`, `skip_reason: "autonomous: <cause>"`, logged, report-only. Teardown: stop every process the phase started, at phase end or on conversion. | ≤3 attempts → convert to skipped; phase enabled by an explicit `--e2e` flag → budget hard stop instead, (a) options: "Provide base_url or start command" / "Finalize without it" / "Stop workflow" |
| `dev.P12.exit` | Phase 12 gate (~L507) | The verifier ran → add Phase 12 to `completed_phases` (report-only), so the Phase 13 precondition holds; converted to skipped → no entry needed. Then continue. The report shows failures → one `auto_decisions` entry (basis `default`, `reason` = the failure counts by severity and the report path), listed in the final block under warnings proceeded with. One exception: an explicit `--e2e` with critical failures (next column) leaves Phase 12 out of `completed_phases` until the hard stop is answered. | Phase 12 enabled by an explicit `--e2e` flag and the report has critical failures → immediate hard stop, with no code edits after Phase 11 and no counter. (a) options: "Proceed with known E2E failures" (add Phase 12 to `completed_phases`, then continue) / "Stop workflow" / "Return to Phase 11" (re-enter its fix loop with the E2E criticals as issues; Phase 12 runs again after it). Otherwise (without an explicit `--e2e`, or no critical) none — Phase 12 stays complete, report-only. |
| `dev.P13.base-url` | Phase 13 prompt (~L521) | As `dev.P12.base-url` (including teardown and the could-not-run rule), converting `user_docs_enabled`; the generator ran → mark Phase 13 complete, report-only | As `dev.P12.base-url`; explicit `--user-docs` → hard stop instead |
| `dev.P13.exit` | Phase 13 gate (~L529) | Continue | — |
| `dev.P14.unresolved` | Phase 14 (~L544) | "Run Phase N now (Recommended)", once per unresolved phase | Still unresolved → Phases 12/13 convert to skipped (hard stop when an explicit `--e2e` / `--user-docs` flag enabled it); any other phase → hard stop |
| `dev.P14.commit` | Phase 14 step 4 (~L550) | Print the commit template only; never commit | — |
| `dev.recovery` | § Auto-Recovery (~L659-670) | § Shared: Retry-Budget Override | Per that section |
| `dev.verify-prereq` | Verifier "report and stop" (verifier ~L81) | The verifier reports as today; development treats that report as an immediate hard stop | Immediate |
| `fw.state.malformed` | Operator Visibility rule 4 (~L96); patterns § 4 Write Rule | Immediate hard stop | Immediate |
| `fw.resume.prereq` | patterns § 5 Resume Logic (~L396) | Immediate hard stop | Immediate |

---

## Executor

Consumer: `skills/implementation-plan-executor/SKILL.md`. Entries log with `phase: phase-8`.

| Site | Location (~line) | Default | Budget / stop |
|---|---|---|---|
| `exec.read-flag` | Phase 2 entry, beside "`--sequential` Opt-Out", in the executor's Autonomous Mode subsection (~L132-141) | Read `orchestrator.options.autonomous`; when `true`, read the Shared sections and this one | — |
| `exec.locate` | Phase 1 step 1 "Get path from context or user" (~L33) | Not resolved by the executor: this ask comes before the Phase 2 flag read. Development always passes the task path when it invokes the executor, so the ask never fires under the orchestrator; a standalone run has no flag and asks as today. | Development about to invoke the executor without a task path → immediate hard stop (development side) |
| `exec.tests-missing` | Test-Driven Enforcement (~L312-323) | "Complete tests first" (re-dispatch for N.1). Never "Accept with justification". | 3 attempts → hard stop |
| `exec.group-failure` | Error Handling § Subagent Failure (~L378-396) | Fix identified → apply it; else "Retry group" with a fresh subagent. Never "Rollback changes" or "Complete manually". The next wave waits for resolution (~L125). | 3 genuine attempts per group (`exec.group-failure.<group>`) → hard stop in the four-option form |
| `exec.test-failure` | Error Handling § Test Failure (~L398-405) | Diagnose → fix → re-run | 3 attempts → hard stop |
| `exec.log` | Progress Tracking § Work-Log Updates (~L335) | Each auto-answer goes to `work-log.md` and to `auto_decisions` with `phase-8` | — |

---

## Verifier

Consumer: `skills/implementation-verifier/SKILL.md`. Entries log with `phase: phase-11`.

| Site | Location (~line) | Default | Budget / stop |
|---|---|---|---|
| `verify.orchestrator` | Invocation Context, orchestrator mode (~L49) | With the key `true`, orchestrator mode never prompts | — |
| `verify.null-option` | Phase 2 Step 1, `null` option (~L96) | Treat as `true`, log it, never prompt (a backstop — Phase 10 already wrote `true`) | — |
| `verify.standalone` | Standalone mode (~L50, ~L97) | Unchanged: standalone runs have no state, so the mode cannot be active | — |

---

## Quick-dev

Consumer: `skills/quick-dev/SKILL.md`. Stateless: every row applies only if this run's Step 1 found the token. Resolution order, the never-auto list, genuine attempts and the hard-stop procedure are § Shared: Resolution and § Shared: Hard Stops (attempts counted in the conversation); the final block format is in § Shared: State & Audit Trail.

| Site | Location (~line) | Default |
|---|---|---|
| `qdev.token` | Step 1 (~L13) | Apply the § 2.2 token rule and strip the token. Nothing left → ask as today (launch-time ask). |
| `qdev.discretionary` | Step 2 (~L15) | No clarifying asks; take the best-evidence interpretation and record it as an assumption |
| `qdev.summary` | Step 4 (~L22) | Add an "Autonomous decisions" block next to the Standards Compliance Checklist |
| `qdev.compaction` | — | After compaction there is no state to re-read: fall back to asking for the rest of the conversation and say so once |

---

## Quick-bugfix

Consumer: `skills/quick-bugfix/SKILL.md`. Same pointer rule and condition as § Quick-dev: rows apply only if this run's Step 1 found the token.

| Site | Location (~line) | Default | Budget / stop |
|---|---|---|---|
| `qbug.token` | Step 1 (~L38-47) | Apply the § 2.2 token rule and strip the token. No description → scan the session context, else ask (launch-time ask). | — |
| `qbug.escalate` | Step 3 escalation (~L75-85) | With 2+ signals: print the Step 3 analysis in the conversation, then invoke `maister:development` via the Skill tool with args = `"<one-line bug description>" --autonomous` — description quoted, token trailing outside the quotes. The analysis and file lists stay in the conversation, not in args. Log it and end quick-bugfix. | — |
| `qbug.plan-mode` | Step 4 (~L87-132) | Skip EnterPlanMode/ExitPlanMode; print the plan inline with all 5 mandatory sections, then proceed | — |
| `qbug.red-passes` | Step 5 (~L147-153) | Re-investigate conditions, inputs and environment. Never fix a bug that was not reproduced. | 3 genuine attempts → hard stop |
| `qbug.fix-budget` | Step 6 (~L170-175) | After 3 failed fix iterations → escalate as `qbug.escalate`; the printed analysis lists the files left modified (no rollback) | — |
| `qbug.summary` | Step 7 (~L175) | Add an "Autonomous decisions" block | — |
| `qbug.compaction` | — | As `qdev.compaction` | — |

---

## Router & classifier

Consumers: `commands/work.md` and `agents/task-classifier.md`. The router is stateless; the classifier knows the run is autonomous only from its prompt.

| Site | Location (~line) | Default |
|---|---|---|
| `work.token` | work.md Step 1 (~L73-88), before folder detection | Apply the § 2.2 token rule to the raw input, strip the token, and run folder detection on the remainder. Carry the token to Step 2 and Step 3. Empty remainder → the no-argument prompt (~L90-91), keeping the token. |
| `work.classify` | Step 3 item 1 (~L160-179; prompt ~L166-169) | Add a new prompt line, exactly: `autonomous: true — do not ask the user; return the highest-confidence classification` |
| `work.route` | Step 3 item 3 (~L189-205) | development → append `--autonomous` after the description in args. Any other type → print "<type> does not support --autonomous; running interactively" and route without it. Compound task → route the full description, as today (~L198). |
| `work.resume` | Step 2 item 5 (~L142-154); Resume Skill Reference (~L250) | Forward the token in `[flags]`; add a resume example line and `[--autonomous]` to the development row |
| `work.no-arg` | Step 1 (~L90-91) | Unchanged (launch-time ask) |
| `work.menu` | Step 2 status menu (~L114) | Unchanged (launch-time ask) |
| `work.classify-fail` | Error Handling § Classification Fails (~L217-232) | Unchanged (launch-time ask) |
| `classifier.issue-fetch` | task-classifier Phase 1 (~L86) | Prompt says `autonomous: true` and no tool can fetch the issue → classify from the identifier plus the given text; note it in `reasoning`. Never ask. |
| `classifier.tie` | Phase 3 tie-break (~L174) | Still tied after context analysis → development if it is among the tied types, else the first tied type; note it in `reasoning` |
| `classifier.confirm` / `classifier.multi-task` / `classifier.vague` | Phase 4 confirmation (~L203, ~L227); Special Cases compound (~L319), vague (~L321-342), unclear (~L360) | Skip every confirmation and override prompt. Multiple tasks → classify the primary one. Vague → best guess with its (low) confidence. Note it in `reasoning`. |
