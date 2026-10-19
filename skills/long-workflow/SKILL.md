---
name: long-workflow
description: 启动、暂停、恢复或检查 Long Workflow Agent；用 LangGraph 持久化状态、独立 Codex Worker 对话和指定网页 Reviewer 推进已批准项目。仅适用于用户授权的长期分阶段工作流。
---

# Long Workflow Agent · App launcher

This skill is the thin App entrypoint for a Python/LangGraph agent runtime.
The runtime, not this prompt, owns stage transitions, budgets and durable pause control.

Resolve runtime location from `LONG_WORKFLOW_HOME`, this installed skill's local
`runtime-location.txt` if present, or the active checkout containing `orchestrator/` and
`pyproject.toml`. If none is available, ask for the runtime checkout. Never copy another
user's absolute paths, conversation IDs or reviewer settings.

Use `<runtime>/.venv/Scripts/python.exe -X utf8 -m orchestrator --config <absolute YAML>`
on Windows. Run from the runtime checkout or use its installed `long-workflow` CLI.
Configuration/run come from this chat or the user's explicit selection. Do not use an
acceptance fixture for an unrelated project. Read actual approved Proposal/Batch inputs.
If an existing project has only legacy JSON workflow state and no runtime checkpoint,
do not infer a new run or replay its Workers. Report that explicit configuration/state
migration is needed; the runtime does not automatically import old skill state.

- **Start:** inspect existing state; `start RUN` only for a new authorized run. Otherwise
  drive its bridge. A completed run does not restart. Register/reuse a native thread
  heartbeat for the control chat using configured interval and exact config/run.
- **Pause:** `pause RUN --reason TEXT` first; pause the heartbeat; drive pause confirmation.
  PAUSE_REQUESTED is not confirmed idle. USER_PAUSED requires actual Worker quiescence.
- **Resume:** only an explicit human request to resume authorizes `resume RUN`. Reconcile
  real Worker state, unknown effects and Git snapshot first; continue the bound Worker.
  Then reactivate the existing heartbeat. No scheduled wake can authorize resume.
- **Scheduled wake / App restart:** obtain actual host delivery/turn identity, then
  `wake RUN --delivery-id ID --trigger schedule|startup --output PACKET`. This records an
  observation, never resumes. Read existing bindings and unknown claims before any effect.
  USER_PAUSED stops dispatch and advancement regardless of scheduler status; pause that
  heartbeat. PAUSE_REQUESTED allows only existing-effect reconciliation and pause handling.
  RUNNING drives the bridge; completion pauses the heartbeat. Never create a second Worker
  or resend a message just because the App restarted.
- **Inspect:** `status RUN`, `events RUN`, `bridge RUN`.

Read [host-bridge.md](references/host-bridge.md) before executing platform effects.
It defines claim/bind/observe, review evidence, unique control signals and recovery.

User authorization to execute a configured run includes its Worker creation, in-scope
messages and heartbeat. It does not expand to another project or destination. A scheduled
prompt is a continuation of that authorization and cannot remove a saved human pause.

App and browser tools belong to the host, not Python. Local unattended operation requires
the computer and App to be running, project storage accessible, and required login/tool
connections available. App shutdown does not execute local work; recovery occurs after
relaunch and the next actual scheduled delivery. Do not promise offline/cloud execution.

Keep raw host receipts, IDs, review replies, screenshots, databases and absolute paths in
local ignored state. Public reports use synthetic labels and explicit tested boundaries.
