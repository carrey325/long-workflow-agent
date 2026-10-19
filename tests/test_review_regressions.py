import pytest
from conftest import finish_worker, git

from orchestrator.runtime import Runtime


def bound_worker(rt):
    rt.start('run')
    original_claim = rt.claim

    def claim_and_bind(action_id):
        action = original_claim(action_id)
        rt.bind(action_id, 'visible-run:B1:A1', 'app thread')
        return action

    rt.claim = claim_and_bind
    return finish_worker(rt)


def test_resume_rechecks_worker_did_not_restart_after_pause(project):
    rt = Runtime(project)
    action, _ = bound_worker(rt)
    epoch = rt.pause('run', 'pause')['control']['epoch']
    rt.observe(action['id'], 'QUIESCENT', 'App idle', epoch)
    rt.confirm_pause('run', epoch, 'App idle')
    rt.observe(action['id'], 'RUNNING', 'User started another turn in App', epoch)
    assert rt.status('run')['status'] == 'PAUSE_REQUESTED'
    with pytest.raises(ValueError, match='quiescent|confirmed'):
        rt.resume('run')


def test_blocker_control_cannot_process_done_while_worker_active(project):
    rt = Runtime(project)
    action, result = bound_worker(rt)
    bad = dict(result, commit_sha='a' * 40)
    rt.receipt(action['id'], bad, 'bad receipt', expected_revision=1)
    rt.observe(action['id'], 'QUIESCENT', 'App idle', 0)
    blocked = rt.tick('run')
    assert blocked['status'] == 'BLOCKED'
    rt.receipt(action['id'], result, 'corrected receipt', expected_revision=2)
    rt.observe(action['id'], 'RUNNING', 'Worker active', 0)
    with pytest.raises(ValueError, match='quiescent|active'):
        rt.control('run', {'action': 'RECHECK_AND_RESUME',
                          'blocker_id': blocked['blocker']['id'], 'user_message': 'retry'})


def test_unresolved_signal_blocks_tick_even_with_completed_receipt(project):
    rt = Runtime(project)
    rt.start('run')
    action = rt.pending()[0]
    rt.claim(action['id'])
    rt.bind(action['id'], 'visible-run:B1:A1', 'app thread')
    epoch = rt.pause('run', 'pause')['control']['epoch']
    rt.observe(action['id'], 'QUIESCENT', 'App idle', epoch)
    rt.confirm_pause('run', epoch, 'App idle')
    epoch = rt.resume('run')['control']['epoch']
    rt.claim_signal(action['id'], 'RESUME', epoch)
    rt.claim = lambda _id: action  # Already claimed; fixture only supplies candidate files.
    finish_worker(rt)
    rt.observe(action['id'], 'QUIESCENT', 'Idle, but uncertain message still outstanding', epoch)
    assert rt.tick('run')['status'] == 'WAITING_WORKER'
    assert rt.bridge('run')['operation'] == 'RECONCILE_SIGNAL'


def test_omitted_commit_change_cannot_reach_review(project):
    rt = Runtime(project)
    rt.start('run')
    action, result = finish_worker(rt)
    (project.repo_path / 'unreported.py').write_text('raise RuntimeError("unreviewed")\n')
    git(project.repo_path, 'add', 'unreported.py')
    git(project.repo_path, 'commit', '-m', 'extra unreported change')
    git(project.repo_path, 'push', 'origin', 'workflow')
    result['commit_sha'] = git(project.repo_path, 'rev-parse', 'HEAD')
    rt.receipt(action['id'], result, 'new receipt', expected_revision=1)
    state = rt.tick('run')
    assert state['status'] == 'BLOCKED'
    assert 'manifest' in state['blocker']['reason']


def test_idle_after_sent_resume_requires_attention_instead_of_duplicate_send(project):
    rt = Runtime(project)
    rt.start('run')
    action = rt.pending()[0]['id']
    rt.claim(action)
    rt.bind(action, 'thread', 'App thread')
    epoch = rt.pause('run', 'pause')['control']['epoch']
    rt.observe(action, 'QUIESCENT', 'App idle', epoch)
    rt.confirm_pause('run', epoch, 'App idle')
    epoch = rt.resume('run')['control']['epoch']
    signal = rt.claim_signal(action, 'RESUME', epoch)
    rt.ack_signal(signal['id'], 'App accepted')
    rt.observe(action, 'QUIESCENT', 'Worker ended with an error, no candidate', epoch)
    assert rt.bridge('run')['operation'] == 'WORKER_NEEDS_ATTENTION'


def test_sealed_worker_restart_blocks_review_and_routes_pause_to_worker(project):
    rt = Runtime(project)
    action, _ = bound_worker(rt)
    rt.observe(action['id'], 'QUIESCENT', 'App idle', 0)
    assert rt.tick('run')['status'] == 'WAITING_REVIEW'
    review_action = rt.pending()[0]['id']
    rt.observe(action['id'], 'RUNNING', 'User restarted sealed Worker', 0)
    assert rt.bridge('run')['operation'] == 'OBSERVE_WORKER'
    with pytest.raises(ValueError, match='quiescent|active'):
        rt.claim(review_action)
    state = rt.pause('run', 'stop restarted worker')
    assert state['status'] == 'PAUSE_REQUESTED'
    epoch = state['control']['epoch']
    packet = rt.bridge('run')
    assert packet['operation'] == 'SEND_PAUSE'
    assert packet['action_id'] == action['id']
    with pytest.raises(ValueError, match='quiescent'):
        rt.confirm_pause('run', epoch, 'not actually stopped')
    signal = rt.claim_signal(action['id'], 'PAUSE', epoch)
    rt.ack_signal(signal['id'], 'App accepted')
    rt.observe(action['id'], 'QUIESCENT', 'App idle again', epoch)
    rt.confirm_pause('run', epoch, 'App idle')
    rt.resume('run')
    assert rt.bridge('run')['operation'] == 'SEND_REVIEW'


def test_no_pending_actions_does_not_hide_a_sealed_active_worker(project):
    rt = Runtime(project)
    action, _ = bound_worker(rt)
    # Crash boundary: verification sealed receipt, next review intent not committed yet.
    rt.store.seal(action['id'])
    assert not rt.pending()
    assert rt.bridge('run')['operation'] == 'OBSERVE_WORKER'
    rt.pause('run', 'pause at crash boundary')
    packet = rt.bridge('run')
    assert packet['operation'] == 'SEND_PAUSE'
    assert packet['action_id'] == action['id']
