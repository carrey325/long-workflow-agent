# 运行、暂停与恢复

配置模板为 `examples/project_config.yaml`。每个项目需要已批准的 Proposal、顺序 Batch、
验收要求、独立 Git 分支和 remote，以及用户指定的既有 Reviewer 对话。
`repo_path` 相对配置文件解析；计划和产物路径相对项目目录解析。

## App 入口

安装 `skills/long-workflow` 到本机 skills 目录。通过 `LONG_WORKFLOW_HOME` 或该目录内的
本地 `runtime-location.txt` 指定 runtime checkout。这个位置文件不得提交。
旧 `$long-workflow-runtime` 名称保留为兼容入口。

旧 skill 的 JSON 状态与项目配置不会自动导入 SQLite。旧项目恢复前需要明确迁移并核对
实际 Worker 和审核历史；不能把旧回执复制成新回执，也不能通过新建 run 重放已有任务。
替换本机入口前保留旧目录备份；现有旧项目的暂停状态不会因安装新入口被解除。

在控制对话中使用：

```text
使用 $long-workflow，按 <配置绝对路径> 启动项目，run_id 为 example-run。
暂停 example-run。
恢复 example-run。
查看 example-run 的进度。
```

启动包含独立可见 Worker、原网页审核与原生 thread heartbeat。平台动作由控制对话
执行宿主工具；Python 保存状态、校验版本和决定下一步。订阅登录的 Worker 不需要
模型 API key，控制对话和 Worker 仍使用订阅额度。

## 状态语义

| 状态 | 含义 |
|---|---|
| RUNNING（control） | 允许在既定计划内驱动 bridge |
| PAUSE_REQUESTED | 已阻止新派发，仍在等待真实停止确认 |
| USER_PAUSED | Worker 已确认闲置，暂停快照已保存 |
| BLOCKED（workflow） | 版本、证据、权限或规则问题，需要对应 blocker 的处理 |
| PROJECT_COMPLETE | 所有 Batch 的执行、产物与独立审核均已通过 |

暂停先持久化控制标志，再暂停 heartbeat，再请求 Worker 收尾。发送暂停指令成功不代表
Worker 已停下；必须确认 turn idle 且没有自己的后台写入。恢复需要人类明确指令、
未决动作核对和暂停快照一致性检查，同一 attempt 沿用原 Worker。

## 定时唤醒与 App 重启

原生 thread heartbeat 保存精确配置、run、入口路径和停止条件。
每次真实投递使用宿主提供的 delivery/turn ID：

```powershell
long-workflow --config <config.yaml> wake example-run --delivery-id <actual-delivery> --trigger schedule --output packet.json
long-workflow --config <config.yaml> wake example-run --delivery-id <actual-startup-delivery> --trigger startup --output packet.json
```

`wake` 只记录观察并返回 bridge 操作，重复 delivery 不重复写事件；它不会调用 resume，
不会创建 run、claim、发消息或直接推进阶段。调度任务只按 RUNNING 分支执行外部操作。
USER_PAUSED 即使遇到迟到或仍启用的定时器，也必须停止。App 重启后先读取原绑定，
核对真实线程、Git 和未决发送，不能重新派发来代替恢复。

本机运行需要电脑开机、App 运行、项目目录可用，浏览器审核还需要可用连接和登录。
关闭 App 期间不会执行本地工作；重新打开后等待下一次真实投递。
详见 [OpenAI scheduled tasks documentation](https://learn.chatgpt.com/docs/automations?surface=app)。
本项目不改变系统休眠、开机启动或账户权限设置。
本轮真实 App 退出/重启验收由用户取消，未验证；上述启动恢复命令描述的是已实现协议，
不能据此宣称完整 App 重启链路已通过。

## CLI 与平台桥接

```powershell
long-workflow --config <config.yaml> start example-run
long-workflow --config <config.yaml> pause example-run --reason 'User requested pause'
long-workflow --config <config.yaml> resume example-run
long-workflow --config <config.yaml> bridge example-run --output packet.json
long-workflow --config <config.yaml> export example-run <local-output-directory>
```

PowerShell wrapper `scripts/workflow.ps1` 提供 start/pause/resume/status/bridge/tick。
CLI 本身不调用 App 或浏览器；实际桥接步骤见
`skills/long-workflow/references/host-bridge.md`。

未知外部发送必须查询真实历史再确认已发生或未发生，不能超时后盲重试。
审核回复必须保存原文并校验 run、request、plan 和 SHA。更正未消费回执需要
`--expected-revision`；已经消费的记录不可改写。批准只能接受当前绑定候选。
