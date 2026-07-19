# Agent guardrails — running veeam-aiops with a smaller / local model

If you drive these tools with a local model (Llama, Qwen, Mistral … via Goose,
Ollama, LM Studio, or any OpenAI-compatible runtime), you will get noticeably
better results with a short system prompt. This page gives you one, and — more
importantly — tells you which guardrails you **no longer need to write**, because
the tool now enforces them itself.

The distinction matters. A guardrail in a prompt is a request. A guardrail in the
harness is a guarantee. Anything below that we could move into the harness, we did.

## What the tool now enforces — do not waste prompt budget on these

| You might be tempted to prompt | Why you don't need to |
|---|---|
| "Work read-only, never start/stop a job or restore anything" | Set `VEEAM_READ_ONLY=1`. The 8 write tools (`job_start`, `job_stop`, `job_retry`, `job_enable`, `job_disable`, `session_stop`, `start_vm_restore`, `undo_apply`) are then **not registered at all** — they never appear in the tool list, so the model cannot call one even if it tries. The `@governed_tool` harness independently refuses writes, so the CLI is covered too. 17 read tools remain. |
| "Don't invent a value when a field is missing" | A field the VBR API did not return comes back as `null`, never as `""`. A job with no `lastResult` yet, a session with no verdict, a proxy with no reported host — all report `null`, and only a genuinely empty upstream value comes back as `""`. Absent and empty are distinguishable in the payload. |
| "Tell me if the output was cut off" | `undo_list` — the one read with a server-side cap — returns `{"undos": [...], "returned": N, "limit": L, "truncated": true/false}`. Truncation is measured (one extra row is fetched), not guessed from a length coincidence. Every other read returns everything VBR returned for that collection; nothing is silently trimmed behind your back. |
| "Preserve the ordering / tell me what's most urgent" | `job_failure_rca` and `repository_capacity_rca` findings carry an explicit 1-based `rank`, worst-first. Priority is in the payload, not implied by list position. |
| "Explain why you flagged that repository / job" | Every finding carries the measured signal that tripped it — the session `result` and the matched error substring, or the free-space percentage against the threshold — plus a concrete `cause` and `action`. The heuristics are transparent, not a verdict. |
| "Confirm before anything destructive" | `job_stop`, `session_stop` and `restore start` require a `--dry-run`-able preview plus double confirmation at the CLI; the MCP write tools take `dry_run=True`. `start_vm_restore` is tagged `high` risk and needs a named approver (`VEEAM_AUDIT_APPROVED_BY`). |
| "Remember how to put it back" | Writes with a clean inverse (`job_start`/`job_stop`, `job_enable`/`job_disable`, `job_retry`) record an undo token — list them with `undo_list`, replay with `undo_apply`. The irreversible ones (`start_vm_restore`, `session_stop`) record none and say so. |
| "Log what you did" | Every governed call is audited to `~/.veeam-aiops/audit.db` regardless of what the model says it did. |

## What still needs a prompt

These are model-behaviour problems the harness cannot fix from the outside.
Copy this into your agent's system prompt:

```text
You operate a Veeam Backup & Replication environment through the veeam-aiops
MCP tools.

TOOL USE
- Before answering any question about the current Veeam environment, you MUST
  call a tool. Never answer from memory or assumption.
- Actually invoke the tool. Do not describe the call you would make, and do not
  emit an example JSON response in place of calling it.
- If a tool call fails, report the real error verbatim. Never fill the gap with
  a plausible-sounding answer.

READING RESULTS
- Read the whole result before concluding. If a result contains a "truncated"
  field that is true, say so and re-run with a higher limit instead of treating
  the partial result as complete.
- A null field means the VBR API did not return that value. Report it as "not
  available" — never infer it. A session with a null result has not finished,
  which is not the same as having succeeded.
- Report values exactly as returned. Do not normalise, translate, or prettify
  job status strings, session results (Success / Warning / Failed / None), or
  identifiers.
- When a diagnose result has findings, work in "rank" order and cite the
  measured number in each finding's "detail".

SCOPE
- Separate observation from interpretation. State what the tools returned, then
  any interpretation, clearly marked as such.
- Do not assert a backup failure, a capacity problem, or a missed RPO unless a
  tool result supports it.
- Do not add generic advice that does not follow from the tool output.
- Do not confuse a job id with a session id, a session id with a restore point
  id, or a backup id with the repository it lives on. They are different
  objects with different tools.
```

## Recommended setup for a local model

```bash
# Read-only until you trust the setup — this is enforced, not advisory.
export VEEAM_READ_ONLY=1
veeam-aiops doctor
```

Then, when you are ready to allow writes, unset it and set an approver so the
high-risk tier has an accountable name on it:

```bash
unset VEEAM_READ_ONLY
export VEEAM_AUDIT_APPROVED_BY="your.name@example.com"
export VEEAM_AUDIT_RATIONALE="scheduled maintenance window 2026-07-20"
```

## Veeam-specific notes

These are the places a smaller model most often goes wrong against a VBR server,
and what to do about them:

- **Jobs and restores are asynchronous.** `job_start`, `job_retry` and
  `start_vm_restore` return immediately; the work happens in a *session*. Progress
  belongs to `session_get`, not to the job. Tell the model to start once and poll
  the session — the runaway budget guard will trip on a re-issue loop, but a
  wasted trip is still a wasted trip.
- **Four id namespaces look alike.** Job ids, session ids, backup ids and restore
  point ids are all opaque strings. `backup_object_list` takes a *backup* id;
  `restore_list_points` filters by a *backup* id and returns *restore point* ids;
  `start_vm_restore` takes a *restore point* id. Models routinely pass the wrong
  one — prefer chaining the tools (`backup_list` → `restore_list_points`) over
  letting the model reconstruct an id.
- **"Warning" is a real failure state.** A Veeam session result of `Warning` is
  flagged by `job_failure_rca` alongside `Failed`. A model that treats Warning as
  success will report a healthy backup estate that isn't one.
- **A disabled job is not a failing job.** `job_list` reports `isDisabled`
  separately from `lastResult`; a disabled job simply has not run.
- **`start_vm_restore` cannot be undone.** It overwrites or creates a VM and
  records no undo token. It is `high` risk deliberately — leave the approver gate
  on. It is also a documented skeleton: the exact endpoint and payload differ per
  restore type (instant recovery vs full restore vs restore-to-new-location) and
  per Veeam version, so validate it against your environment before trusting it.
- **Repository capacity is best-effort.** VBR only exposes capacity/free on the
  repository *states* endpoint, and not for every repository type. When
  `repository_capacity_rca` cannot compute a free percentage it reports `null`
  and skips the repository rather than guessing — a null there is not "0% free".

## If your model still struggles

Some behaviours are model-capacity limits rather than prompt problems:

- **Multi-tool workflows time out or drift.** Prefer `overview` and the
  `*_rca` diagnose tools — they do the multi-step correlation inside one call, so
  the model does not have to chain reads and keep job/session ids straight.
- **The model ignores later tool results in a long context.** Ask narrower
  questions; use `job_get` / `session_get` on one object rather than pulling the
  whole session list and reasoning over it.
- **The model describes calls instead of making them.** This is usually a
  runtime/tool-calling-format mismatch, not a prompt problem — check that your
  client advertises the tools in the format your model was trained on.

Feedback on running this with a specific local model is genuinely useful —
open an issue at
[github.com/AIops-tools/Veeam-AIops](https://github.com/AIops-tools/Veeam-AIops/issues)
with the model, runtime, and what went wrong.
