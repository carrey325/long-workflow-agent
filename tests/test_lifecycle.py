import pytest
from conftest import finish_worker, review

from orchestrator.runtime import Runtime


def test_pause_before_dispatch_persists_and_prevents_claim(project):
    rt = Runtime(project)
    rt.start('run')
    action = rt.pending()[0]
    assert rt.pause('run', 'user pause')['status'] == 'USER_PAUSED'
    rt = Runtime(project)
    assert rt.tick('run')['status'] == 'USER_PAUSED'
    with pytest.raises(ValueError, match='paus'):
        rt.claim(action['id'])
    assert rt.resume('run')['status'] == 'WAITING_WORKER'
    rt.claim(action['id'])


def test_unknown_launch_prevents_false_pause_confirmation(project):
    rt = Runtime(project)
    rt.start('run')
    rt.claim(rt.pending()[0]['id'])
    state = rt.pause('run', 'pause')
    assert state['status'] == 'PAUSE_REQUESTED'
    with pytest.raises(ValueError, match='unresolved|unknown|quiescent'):
        rt.confirm_pause('run', state['control']['epoch'], 'claimed, no observed task')
    with pytest.raises(ValueError, match='confirm|requested'):
        rt.resume('run')


def test_same_worker_dirty_checkpoint_restart_and_stale_ack(project):
    rt = Runtime(project)
    rt.start('run')
    action = rt.pending()[0]
    rt.claim(action['id'])
    rt.bind(action['id'], 'thread-one', 'codex://threads/thread-one')
    (project.repo_path / 'partial.txt').write_text('work in progress')
    paused = rt.pause('run', 'pause mid-attempt')
    epoch = paused['control']['epoch']
    with pytest.raises(ValueError, match='quiescent'):
        rt.confirm_pause('run', epoch, 'not observed')
    rt.observe(action['id'], 'QUIESCENT', 'App final: paused; no background work', epoch)
    assert rt.confirm_pause('run', epoch, 'App idle')['status'] == 'USER_PAUSED'
    rt = Runtime(project)
    assert rt.resume('run')['status'] == 'WAITING_WORKER'
    assert rt.store.binding(action['id'])['external_id'] == 'thread-one'
    assert len(rt.pending()) == 1
    newer = rt.pause('run', 'pause again')
    with pytest.raises(ValueError, match='stale'):
        rt.confirm_pause('run', epoch, 'old observation')
    assert newer['control']['epoch'] > epoch


def test_edits_after_confirmed_pause_block_resume(project):
    rt = Runtime(project)
    rt.start('run')
    rt.pause('run', 'pause')
    (project.repo_path / 'new.txt').write_text('unexpected edit')
    with pytest.raises(ValueError, match='checkout|changed'):
        rt.resume('run')
    assert rt.status('run')['status'] == 'USER_PAUSED'


def test_review_receipt_while_paused_is_collected_without_advancing(project):
    rt = Runtime(project)
    rt.start('run')
    finish_worker(rt)
    rt.tick('run')
    action = rt.pending()[0]
    rt.claim(action['id'])
    rt.bind(action['id'], project.reviewer.url, project.reviewer.url + '#request')
    assert rt.pause('run', 'pause review')['status'] == 'USER_PAUSED'
    payload = action['payload']
    decision = {k: payload[k] for k in
                ['run_id', 'stage_id', 'request_id', 'commit_sha', 'plan_version']}
    decision.update(decision='APPROVED', evidence_status='SUFFICIENT',
                    inspected_evidence=['result.txt'], findings=[],
                    worker_instruction='', limitations=[])
    rt.receipt(action['id'], decision, project.reviewer.url + '#response')
    assert Runtime(project).tick('run')['status'] == 'USER_PAUSED'
    rt.resume('run')
    assert rt.tick('run')['status'] == 'PROJECT_COMPLETE'


def test_bound_worker_identity_must_match_receipt(project):
    rt = Runtime(project)
    rt.start('run')
    action = rt.pending()[0]
    rt.claim(action['id'])
    rt.bind(action['id'], 'actual-thread', 'app locator')
    with pytest.raises(ValueError, match='identity'):
        rt.receipt(action['id'], {'status': 'DONE', 'worker_id': 'other'}, 'completion')
    with pytest.raises(ValueError, match='identity|bound'):
        rt.bind(action['id'], 'another-thread', 'different locator')
    with pytest.raises(ValueError, match='bound'):
        rt.reconcile_absent(action['id'], 'incorrectly claiming it never launched')


def test_completed_run_cannot_be_paused(project):
    rt = Runtime(project)
    rt.start('run')
    finish_worker(rt)
    rt.tick('run')
    review(rt)
    rt.tick('run')
    with pytest.raises(ValueError, match='terminal|complete'):
        rt.pause('run', 'too late')
