# 发布脱敏

运行数据默认位于项目 Git common directory 的 `long-workflow/` 下。
`export` 是完整本地审计导出，会包含真实配置、绑定、定位、审核原文路径和事件。
它不是公开报告；不能直接提交或上传。

公开发布采用代码与文档的显式文件清单，排除：

- 本地运行目录、SQLite、Git 元数据、截图、原始对话、审核包与回执。
- 真实 conversation/thread/message IDs、本机用户目录、配置授权文本和位置文件。
- 凭据、环境文件、个人资料和本地安装备份。

公开验收报告仅使用合成标签、步骤和经过验证的结果；真实证据只在本地保留。

提交前检查 Git index 中的全部 blob：

```powershell
python scripts/check_release_privacy.py <publish-checkout> --deny-file <local-private-literals.txt>
```

检查包含运行路径、会话 ID、常见凭据、个人目录及可选的本地私有字符串列表。
输出仅包含文件名和规则，不回显命中的秘密。检查对象是待提交内容，即使工作区文件
后来被清理，也不能掩盖仍在 index 中的旧内容。

扫描器是发布检查的一部分，不能识别所有语义上的敏感资料。发布者仍须审阅完整 diff、
确认文件来源与授权、核对公开报告，确保敏感内容从未进入待推送提交。
