import pytest

from orchestrator.runtime import Runtime


def test_scheduled_wake_never_resumes_pause_after_runtime_restart(project):
    rt = Runtime(project)
    rt.start('run')
    paused = rt.pause('run', 'user paused')
    before = rt.actions()
    packet = Runtime(project).wake('run', 'actual-delivery-1', 'schedule')
    assert packet['operation'] == 'PAUSED'
    assert packet['epoch'] == paused['control']['epoch']
    assert rt.actions() == before
    assert rt.status('run')['control']['mode'] == 'USER_PAUSED'
    Runtime(project).wake('run', 'actual-delivery-1', 'schedule')
    events = [e for e in rt.store.events('run') if e['kind'] == 'HOST_WAKE']
    assert len(events) == 1
    assert events[0]['data']['operation'] == 'PAUSED'


def test_startup_reconciles_unknown_worker_and_cannot_confirm_pending_pause(project):
    rt = Runtime(project)
    rt.start('run')
    action = rt.pending()[0]
    rt.claim(action['id'])
    assert Runtime(project).wake('run', 'startup-1', 'startup')['operation'] == 'RECONCILE_ACTION'
    rt.pause('run', 'pause uncertain launch')
    packet = Runtime(project).wake('run', 'schedule-2', 'schedule')
    assert packet['operation'] == 'RECONCILE_ACTION'
    assert rt.status('run')['status'] == 'PAUSE_REQUESTED'
    assert rt.actions()[0]['status'] == 'CLAIMED'


def test_running_wake_returns_same_worker_intent_without_claiming(project):
    rt = Runtime(project)
    rt.start('run')
    before = rt.actions()
    packet = Runtime(project).wake('run', 'schedule-3', 'schedule')
    assert packet['operation'] == 'CREATE_WORKER'
    assert packet['action_id'] == before[0]['id']
    assert rt.actions() == before
    with pytest.raises(ValueError, match='trigger|delivery'):
        rt.wake('run', '', 'schedule')
    with pytest.raises(ValueError, match='trigger|delivery'):
        rt.wake('run', 'x', 'resume')
