"""Create a small real-Worker acceptance project; never simulates an agent or review."""
import argparse
import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def git(*args, cwd=None):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default=str(ROOT / '.runs' / 'live-smoke'))
    parser.add_argument('--reviewer-url', required=True)
    parser.add_argument('--reviewer-model', default='Keep existing page setting')
    args = parser.parse_args()
    TARGET = Path(args.directory).resolve()
    REPO = TARGET / 'project'
    if TARGET.exists():
        raise SystemExit('Live-smoke directory exists; inspect it instead of recreating.')
    REPO.mkdir(parents=True)
    git('init', '--bare', str(TARGET / 'remote.git'))
    git('init', '-b', 'workflow', cwd=REPO)
    git('config', 'user.name', 'Long Workflow Local Acceptance', cwd=REPO)
    git('config', 'user.email', 'workflow@example.invalid', cwd=REPO)
    (REPO / '.gitignore').write_text('__pycache__/\n*.pyc\n', 'utf-8')
    (REPO / 'proposal.md').write_text('''# CSV 汇总工具真实链路验收

用户已批准用一个小项目验证独立 Codex Worker、暂停/恢复和原网页 Reviewer。
实现 Python 标准库 CSV 汇总工具：按 name 汇总整数 amount，以 name 排序输出 JSON。
只处理本地合成数据。Git origin 是本地 bare 仓库，推送用于验证提交可达性，不发布 GitHub。
范围不包含网络服务、真实金融数据、依赖安装或更改控制器。
''', 'utf-8')
    (REPO / 'batch.md').write_text('''# B1：实现并验证

实现 summarize.py，提供 summarize_csv(path) -> dict 与 CLI：
python summarize.py data.csv result.json
返回 {"totals": {name: integer_sum, ...}, "grand_total": integer}，names 按字典序。
输入必须含 name,amount 两列；允许额外列；空白 name、缺失 amount、非整数 amount、
缺失必需表头均抛出 ValueError；空数据（只有表头）返回空 totals 和 0；允许负数和重复 name。
使用 unittest 编写 test_summary.py，至少测试上述规则和命令行输出。
固定 data.csv：name,amount；alice,10；bob,7；alice,-3。
result.json 应为 {"totals":{"alice":7,"bob":7},"grand_total":14}。
写 README.md 解释调用、输入限制和边界。实际执行测试，将完整非空输出存入 validation.txt。
运行 CLI 生成 result.json，提交全部交付物并 git push origin workflow。
唯一验收 check ID 是 csv-summary；其日志为 validation.txt。
manifest 必须包括 summarize.py、test_summary.py、data.csv、result.json、README.md、validation.txt。
控制器暂停是协作式；每个操作批次前检查 control.mode，暂停时保留当前文件、
停止自己启动的工作并结束回合；恢复后从同一对话继续。
''', 'utf-8')
    (REPO / 'data.csv').write_text('name,amount\nalice,10\nbob,7\nalice,-3\n', 'utf-8')
    git('add', '.', cwd=REPO)
    git('commit', '-m', 'Approved local CSV smoke-test plan', cwd=REPO)
    git('remote', 'add', 'origin', str(TARGET / 'remote.git'), cwd=REPO)
    git('push', 'origin', 'workflow', cwd=REPO)
    config = {
        'repo_path': str(REPO), 'branch': 'workflow', 'proposal': 'proposal.md',
        'stages': [{'id': 'B1', 'path': 'batch.md', 'checks': ['csv-summary'],
                    'outputs': ['summarize.py', 'test_summary.py', 'data.csv',
                                'result.json', 'README.md', 'validation.txt']}],
        'worker_normal': {'model': 'app-default', 'effort': 'app-default'},
        'worker_escalated': {'model': 'app-default', 'effort': 'app-default'},
        'reviewer': {
            'url': args.reviewer_url,
            'browser': 'chrome', 'model': args.reviewer_model},
        'authorization': 'User approved v0.2 implementation and real Worker/pause/resume/web review',
        'heartbeat_minutes': 10,
    }
    path = TARGET / 'config.yaml'
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), 'utf-8')
    print(json.dumps({'config': str(path), 'repo': str(REPO)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
