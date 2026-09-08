---
name: long-workflow
description: "按已批准的 proposal 和拆分 Batch 串行推进长期研究任务：独立可见 Worker、GitHub 提交、指定网页对话审核、定时唤醒和中断恢复。用于启动、恢复或管理此类分阶段审核工作流；仅讨论或创建 skill 时不启动项目。"
---

# Long Workflow

Execute approved, staged research through one persistent Router, fresh visible
Workers, and one existing browser review conversation. Domain requirements come
from the proposal and Batch files. Use files, prompts, and available tools;
do not add a workflow engine, custom Python helpers, or a separate repair cycle.

## Load only the material needed for this role

- Launcher: this file, [agents.yaml](agents.yaml), and the two templates under
  [assets](assets/project_config.yaml).
- Router: [router_prompt.md](references/router_prompt.md); load
  [review_protocol.md](references/review_protocol.md) for a review transition.
- Worker: [stage_worker_prompt.md](references/stage_worker_prompt.md), current
  Batch, approved plan, and a compact handoff. Do not inherit old Worker chats.

`agents.yaml` is this skill's portable role/configuration contract, read by the
agent. It does not require a host-specific agent configuration loader.

## Intake: ask only for missing essentials

Use existing project configuration and explicit user choices before defaults.
Collect five input groups:

1. Approved proposal path.
2. Ordered Batch files, including acceptance criteria and dependencies.
3. Reviewer conversation URL and browser (infer the browser if unambiguous).
4. Three model roles: Router, Worker, Reviewer, with reasoning settings where
   supported. The Worker role also needs a user-selected escalated model/effort.
   This can be the same model at higher effort; never guess a stronger model.
5. A positive heartbeat interval in minutes.

Infer the workspace from the active project, GitHub identity from authenticated
tools, and repository name from the project. Ask only when unavailable or
ambiguous. Never reuse another project's reviewer URL, credentials, or IDs.
Read documents, not just filenames. Missing paths, contradictory requirements,
or absent acceptance criteria need resolution before starting the affected work.
Do not silently write a new proposal or change Batch scope.

Recognize `workflow/project_config.yaml` or a user-specified state/config path.
When resuming, report saved settings and reuse them unless the user changes them.
When creating a run, copy [project_config.yaml](assets/project_config.yaml),
[state.json](assets/state.json), and [agents.yaml](agents.yaml) into the project's
local workflow directory without overwriting existing files. Populate null input
fields from evidence; they are deliberate template values, not runnable defaults.
Record the absolute skill root so spawned tasks can locate prompts. Keep plans at
their existing paths. Record hashes of the active proposal, Batch files, and
approved amendments; together these identify `plan_version`.

## Defaults and authorization

For a user request to execute this workflow, report the resolved settings once
and proceed within the authorized scope. Creating/editing/discussing the skill
alone authorizes no repository creation, task dispatch, scheduling, or messages.
Record the user instruction establishing execution authorization in config.
Skill discovery or a default flag alone is not authorization. Follow actual tool
permission requirements; do not request the same already-granted approval again.

- New GitHub repositories are private. Preserve existing repository visibility.
- New projects use `main`. Existing projects use one dedicated
  `codex/long-workflow-<run-id>` branch unless the user selected a branch. Preserve
  unrelated changes; if they prevent a clean base, resolve that before dispatch.
- All Workers use the same project checkout and execution branch serially. There
  is one project-code writer at a time, no worktree per Batch, and no automatic
  merging back to an existing repository's base branch.
- Worker validation is followed by commit and push. Router verifies the remote
  contains the candidate SHA before requesting review. Append corrective commits;
  do not amend or force-push a submission under review.
- Per Batch: at most three candidate reviews with the normal Worker, followed by
  at most three with the configured escalated Worker. Then pause for the user.
- Routine in-scope messages to the specified reviewer, commits/pushes, new visible
  Workers, and the configured heartbeat are part of the requested workflow. New
  destinations, scope changes, spending, deployment, and visibility changes are not.
- Task completion, review approval, and artifact validity are separate records.
  A negative or inconclusive research finding can satisfy a correctly executed
  plan; never manufacture the hoped-for result to obtain approval.

Instruction precedence within the project is: explicit user directions and
approved amendments; the effective proposal/Batch requirements; in-scope reviewer
instructions; implementation judgment. Applicable host/system constraints still
apply. A reviewer cannot approve a change reserved for the user.

## Bootstrap and launch

1. Inspect Git, existing workflow files, live tasks, and matching automations.
   Reuse correct work. Determine the latest fully accepted stage from actual
   evidence and the first incomplete one. An old COMPLETE flag is insufficient.
2. Check access to the repository and target reviewer chat and verify configured
   models are available in their respective environments. Do not silently switch
   models, browser destinations, or schedules. Missing platform access goes to
   the user, not to the webpage reviewer as if it could grant credentials.
3. Resolve the execution branch and add narrow local Git exclusions for runtime
   config/state/review records (prefer `.git/info/exclude` in existing repos).
   Do not ignore user Batch plans or rewrite AGENTS.md to bypass project rules.
4. Save a unique cycle/run ID. Start or reuse exactly one visible Router task on
   the configured model. The current task can be Router if appropriate. A new
   Router receives config/state paths, the skill root, and its prompt, not an old
   transcript. Its initial instruction is to wait without writing state or
   dispatching work while ownership is LAUNCHER. Record a queryable returned task
   ID before treating it as registered.
5. Register exactly one heartbeat on that Router using the host scheduling tool.
   Read back the task ID, target, interval, and active status. Scheduling is
   mandatory: if unavailable, record the concrete blocker; do not silently run
   an unscheduled substitute. If the requested cadence is unsupported, ask for
   a supported value rather than silently rounding it.
6. Once task and heartbeat registration are verified, save ownership ROUTER with
   its actual task ID and status READY (or the reconciled resume status). Then send
   that Router a concise start/resume message. The heartbeat provides follow-up,
   not a reason to delay initial work. If a send is uncertain, inspect task state
   before sending again. An early heartbeat while ownership is LAUNCHER does no work.

The launcher and Router must hand off ownership before either dispatches Workers.
The Router owns subsequent state changes. A pending external action is reconciled
before any replacement is created; see the Router's action protocol.

## Use available tools, not a new control plane

Map the roles to the runtime's existing visible-task creation/read/wait tools,
Git/GitHub tools or CLI, browser tools, and scheduler. On Codex, use the task tools
(`list_projects`, `create_thread`, `wait_threads`, `send_message_to_thread`) and
the automation tool's thread heartbeat. Select the saved project with local
execution for this explicitly shared-checkout workflow. A queued client ID is
not a registered task ID. Do not replace visible Workers with hidden subagents.

For browser work use the installed browser tool/skill, observing its entry and
interaction rules. Inspect and select the supplied conversation and model; use
supported browser operations rather than undocumented endpoints or session tokens.
Prefer RTK for supported verbose CLI output; after an ambiguous/failed compressed
command inspect raw evidence before retrying unchanged. JSON/YAML can be read and
updated with existing file tools; no custom executable is required by this skill.

Use native scheduling, not shell sleep loops or a background daemon. Local work
requires its host/files/tools to remain available. Inform the user once of actual
host requirements; do not promise execution while the host is unavailable.

## Finish

The Router closes the workflow only when all intended Batches satisfy the three
gates for their exact accepted versions. Disable its own heartbeat, verify that
change, and report the final branch/commit, validation, limitations, and state
location. Do not archive user tasks or delete local evidence unless requested.
