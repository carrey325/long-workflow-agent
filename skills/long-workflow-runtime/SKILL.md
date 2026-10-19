---
name: long-workflow-runtime
description: 兼容旧的 Long Workflow Agent 启动名称；启动、暂停、恢复和查看项目时转入规范 long-workflow 入口。
---

# Compatibility launcher

Use the installed `long-workflow` skill, or the sibling
`../long-workflow/SKILL.md` in the runtime checkout. This alias preserves the earlier
`$long-workflow-runtime` invocation without maintaining a second orchestration prompt.
The Python runtime remains the authority for state and human pause control.