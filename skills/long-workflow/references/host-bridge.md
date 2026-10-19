## Drive the host bridge

Call `bridge RUN --output <outside-project packet.json>`. It saves both JSON and a prompt
text file when needed. Read its `operation`, `action_id`, `epoch` and actual external binding.
Before each external effect, claim its action/signal; never act from a stale saved packet.
App tools and browser tools are host capabilities, not Python imports. Do not invent a
network API for them. Do not spawn a subagent as the main Worker.

| operation | Required host action |
|---|---|
| CREATE_WORKER | `claim ACTION`, call App `list_projects`, select the project containing repo_path, `create_thread` with local environment and exact packet prompt. If no saved project contains it, ask user to add it or use an explicitly approved projectless task with the exact repo path in the prompt. Immediately `bind ACTION THREAD_ID --locator codex://threads/THREAD_ID`. Keep the creation tool result. The initial prompt contains a read-only command to obtain the bound identity. Preserve model defaults unless the user explicitly configured an available model. |
| OBSERVE_WORKER | Use `wait_threads` with cursor or `read_thread` for the bound ID. A completed turn alone is insufficient: inspect whether it delivered DONE, acknowledged PAUSED, or needs help. After idle confirmation and no ongoing background writes, `observe ACTION QUIESCENT --epoch E --evidence TEXT`. If DONE, read its result file, verify reported identity, then `receipt ACTION FILE --locator ACTUAL_COMPLETION`. |
| SEND_PAUSE / SEND_RESUME | Recheck actual thread state first. `signal-claim ACTION PAUSE\|RESUME --epoch E`, send a message to the **bound** App thread including the returned unique signal ID, then `signal-ack SIGNAL --evidence ACTUAL_TOOL_RESULT`. Pause messages require the Worker to stop new work, settle its own background commands, report checkpoint and end the turn. Resume continues the same attempt. Sending success is not a pause acknowledgment. |
| CONFIRM_PAUSE | `confirm-pause RUN --epoch E --evidence TEXT`, using actual observations. Until it succeeds, report PAUSE_REQUESTED, not “paused”. |
| SEND_REVIEW | Validate configured URL/model in the browser. Read the entire prompt; `claim ACTION`; send it once through supported browser UI. Immediately `bind ACTION EXACT_CONFIGURED_URL --locator ACTUAL_SENT_MESSAGE`. The prompt contains full textual evidence; never submit only local paths. |
| READ_REVIEW | Wait for the response to finish. Save the exact rendered response to a UTF-8 text file outside tracked project content. `review-import ACTION FILE --locator ACTUAL_RESPONSE`; if packet.receipt_revision is nonzero, include `--expected-revision` with that current revision. When recovering a corrected review with packet.blocker, import the new actual reply first, then use `control` RECHECK_AND_RESUME with that matching blocker ID. Preserve raw replies, including rejected/malformed ones; do not rewrite a judgment into an approval. |
| CORRECT_REVIEW_FORMAT | `signal-claim ACTION REVIEW_FORMAT --epoch E`, send packet.prompt in the same browser conversation with the returned signal ID, then `signal-ack` with actual sending evidence. This corrects exact-path citation formatting for the same candidate, not the review decision. Continue through READ_REVIEW, including revision and blocker handling. One correction per review request across pause/resume epochs; if the corrected reply is still invalid, report the cause instead of looping. |
| TICK | `tick RUN`, then inspect the new bridge operation. |
| PAUSED | Stop; no new work or review messages. Receiving/reconciling already issued effects is allowed. |
| COMPLETE | Pause the run heartbeat, export results, report evidence. |
| BLOCKED | Inspect the blocker and resolve within existing authority; scope/budget changes require the user's decision. |
| WORKER_NEEDS_ATTENTION | A previous continuation ended without a result. Inspect its real error/help request; do not resend the old signal. Do not use a pause/confirm/resume cycle as an automatic retry. Pause the heartbeat and report the actionable condition once; leave durable human pause control unchanged. Only a new explicit human request to continue can authorize rearming the same Worker with a new control epoch. An existing USER_PAUSED run always remains paused until that explicit request. |
| RECONCILE_ACTION | Search actual App/chat history for the unique action ID. If present, bind the existing task/message. Only after evidence that nothing happened use `reconcile-absent ACTION --evidence TEXT`; never blindly create/send again. |
| RECONCILE_SIGNAL | Search bound thread history for signal.id. If sent, `signal-ack`; if proven absent, `signal-ack SIGNAL --absent --evidence TEXT`. An ambiguous outcome stays unresolved. |

`QUIESCENT` means the agent turn is idle and no owned background process can keep writing.
Use precise evidence and epoch. If only one of those is known, do not confirm quiescence.
If a Worker ended because of an error or needs user input, do not reflexively send RESUME:
inspect and resolve that condition first. A result is not an approval; only the controller advances.

## Heartbeat and restart recovery

Use App `automation_update` with kind heartbeat and destination thread (the control chat).
Keep its ID and full tool result in `<runtime state dir>/bridge/heartbeat-RUN.json`.
Before creation after an uncertain result, inspect matching `$CODEX_HOME/automations/*/automation.toml`
by this config path and run ID; reuse an existing match. Never create a duplicate fallback cron job.
Use configured heartbeat_minutes. Prompt must include this skill path, exact config and run,
pause rules, and “stay quiet on unchanged/non-actionable state; notify meaningful progress,
completion, failure or required user action”. Preserve fields when updating an existing heartbeat.
Pause at user pause or completion; activate only after a successful user resume/start.
If the host is unavailable, persist and report the dependency; do not claim autonomous execution.

## Recovery limits

Claims survive crashes and do not expire. The control flag blocks claims and ticks even if a
heartbeat fires late. Resume verifies plan/config and paused Git checkout (including non-ignored
untracked files). A changed checkout requires reconciliation; don't overwrite files or reset Git.
The guard is cooperative, not an OS sandbox. Ignored files and arbitrary external writers are
outside the Git snapshot. Do not run two writers in this checkout.

For a malformed review, inspect saved raw source and check whether a valid complete response
was missed. Exact-path citation corrections have a durable CORRECT_REVIEW_FORMAT route;
other malformed responses require diagnosis and are not automatically resent. Do not invent
a valid decision. Escalate unsupported attachments or unavailable reviewer/model instead of
silently omitting evidence or switching reviewers.
