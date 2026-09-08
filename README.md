# Long Workflow Skill

A reusable agent skill for carrying out approved, multi-stage research with
independent Workers, GitHub checkpoints, browser-based review, and scheduled
follow-up. It uses Markdown prompts, YAML configuration, JSON state, and the
agent environment's existing tools. No custom Python scripts or workflow service.

## Workflow

```text
Proposal + ordered Batches + reviewer conversation + models + wake-up interval
  -> Router
  -> independent visible Worker
  -> implementation, validation, commit and push
  -> review of the exact candidate commit
  -> approval: next Batch
  -> changes required: fresh Worker on the same Batch
```

Each Batch allows up to three candidate reviews with the normal Worker and three
more with a user-selected escalated Worker. Unresolved work then pauses for the
user. Completion, review approval, and artifact validity are tracked separately.

## Use

Place the skill files in your agent's skill directory under `long-workflow`.
For Codex, the usual personal location is `~/.codex/skills/long-workflow/`.
Invoke `$long-workflow` and provide:

1. The approved proposal.
2. Ordered Batch plans with acceptance criteria.
3. The existing browser review conversation.
4. Router, Worker, and Reviewer model settings, including the Worker's escalation setting.
5. The required wake-up interval.

The skill asks for missing essentials and reports resolved settings. Saved choices
are reused unless changed by the user. The host must provide visible-task,
Git/GitHub, browser, and scheduling tools; `agents.yaml` is a portable role contract
read by the skill, not a universal host registration format.

New research repositories default to private; explicit user choices take
precedence. Review approval is bound to the stage, plan version, request ID, and
commit SHA. Reviewers cannot independently expand user authorization or approve
material scope changes. Pending external actions are reconciled before retrying.

## Files

| File | Purpose |
| --- | --- |
| [SKILL.md](SKILL.md) | Intake, defaults, bootstrap, and tool routing |
| [agents.yaml](agents.yaml) | Generic roles and model settings |
| [Router prompt](references/router_prompt.md) | State transitions, scheduling, and recovery |
| [Worker prompt](references/stage_worker_prompt.md) | Stage execution, validation, and handoff |
| [Review protocol](references/review_protocol.md) | Authority, evidence, and version-bound decisions |
| [Project configuration](assets/project_config.yaml) | Per-project configuration template |
| [State](assets/state.json) | Empty persistent-state template |

The templates contain no project data or credentials. Structure and configuration
checks have passed; a live end-to-end scheduling and browser-review run has not
yet been performed. Installing the skill does not start a workflow.
