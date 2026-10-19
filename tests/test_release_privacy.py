import subprocess
import uuid

from scripts.check_release_privacy import scan_staged


def stage(repo, name, text):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    subprocess.run(['git', 'add', '--', name], cwd=repo, check=True, capture_output=True)


def test_scanner_checks_index_not_sanitized_working_copy(tmp_path):
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    stage(tmp_path, 'README.md', 'https://chatgpt.com/c/' + str(uuid.uuid4()))
    (tmp_path / 'README.md').write_text('sanitized working copy', encoding='utf-8')
    hits = scan_staged(tmp_path)
    assert any(h['path'] == 'README.md' and h['rule'] == 'conversation-id' for h in hits)
    assert all(set(h) == {'path', 'rule'} for h in hits)


def test_private_run_paths_and_explicit_private_literals_are_rejected(tmp_path):
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    stage(tmp_path, '.runs/result.txt', 'local evidence')
    stage(tmp_path, 'docs/report.md', 'private-literal-for-test')
    hits = scan_staged(tmp_path, ['private-literal-for-test'])
    assert {'path': '.runs/result.txt', 'rule': 'private-runtime-path'} in hits
    assert {'path': 'docs/report.md', 'rule': 'explicit-private-literal'} in hits


def test_portable_source_and_synthetic_placeholders_pass(tmp_path):
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    stage(tmp_path, 'README.md', 'https://chatgpt.com/c/YOUR_REVIEW_CONVERSATION')
    stage(tmp_path, 'src/main.py', 'print("portable runtime")\n')
    assert scan_staged(tmp_path) == []
