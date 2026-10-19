import json

import pytest
from conftest import finish_worker

from orchestrator.runtime import Runtime


def worker(rt):
    rt.start('run')
    action = rt.pending()[0]['id']
    rt.claim(action)
    rt.bind(action, 'thread-one', 'app thread-one')
    return action


def test_bridge_never_blindly_relaunches_claimed_action(project):
    rt = Runtime(project)
    rt.start('run')
    instruction = rt.bridge('run')
    assert instruction['operation'] == 'CREATE_WORKER'
    rt.claim(instruction['action_id'])
    assert Runtime(project).bridge('run')['operation'] == 'RECONCILE_ACTION'
    rt.bind(instruction['action_id'], 'thread-one', 'app thread-one')
    assert rt.bridge('run')['operation'] == 'OBSERVE_WORKER'


def test_pause_resume_messages_are_claimed_once_and_survive_restart(project):
    rt = Runtime(project)
    action = worker(rt)
    epoch = rt.pause('run', 'test')['control']['epoch']
    assert rt.bridge('run')['operation'] == 'SEND_PAUSE'
    signal = rt.claim_signal(action, 'PAUSE', epoch)
    assert Runtime(project).bridge('run')['operation'] == 'RECONCILE_SIGNAL'
    with pytest.raises(ValueError, match='claimed|reconcil'):
        rt.claim_signal(action, 'PAUSE', epoch)
    rt.ack_signal(signal['id'], 'App accepted pause message')
    rt.observe(action, 'QUIESCENT', 'App turn ended, no background process', epoch)
    rt.confirm_pause('run', epoch, 'observed')
    assert rt.bridge('run')['operation'] == 'PAUSED'
    rt.resume('run')
    instruction = rt.bridge('run')
    assert instruction['operation'] == 'SEND_RESUME'
    assert instruction['external_id'] == 'thread-one'
    resume_signal = rt.claim_signal(action, 'RESUME', instruction['epoch'])
    rt.ack_signal(resume_signal['id'], 'App accepted resume')
    assert Runtime(project).bridge('run')['operation'] == 'OBSERVE_WORKER'


def test_inflight_resume_blocks_pause_until_reconciled(project):
    rt = Runtime(project)
    action = worker(rt)
    epoch = rt.pause('run', 'first')['control']['epoch']
    rt.observe(action, 'QUIESCENT', 'App idle', epoch)
    rt.confirm_pause('run', epoch, 'App idle')
    state = rt.resume('run')
    signal = rt.claim_signal(action, 'RESUME', state['control']['epoch'])
    state = rt.pause('run', 'again')
    epoch2 = state['control']['epoch']
    rt.observe(action, 'QUIESCENT', 'App idle before uncertain resume arrives', epoch2)
    with pytest.raises(ValueError, match='signal|unresolved'):
        rt.confirm_pause('run', epoch2, 'cannot yet be sure')
    rt.ack_signal(signal['id'], 'located already sent resume')
    with pytest.raises(ValueError, match='quiescent'):
        rt.confirm_pause('run', epoch2, 'resume can run')


def test_review_packet_contains_actual_evidence_and_parses_original_reply(project):
    rt = Runtime(project)
    rt.start('run')
    finish_worker(rt)
    rt.tick('run')
    action = rt.pending()[0]
    packet = rt.bridge('run')
    assert packet['operation'] == 'SEND_REVIEW'
    assert 'candidate run:B1:A1' in packet['prompt']
    assert 'result-check: PASS' in packet['prompt']
    assert action['payload']['commit_sha'] in packet['prompt']
    rt.claim(action['id'])
    rt.bind(action['id'], project.reviewer.url, 'sent message')
    result = {k: action['payload'][k] for k in
              ['run_id', 'stage_id', 'request_id', 'commit_sha', 'plan_version']}
    result.update(decision='APPROVED', evidence_status='SUFFICIENT',
                  inspected_evidence=['result.txt'], findings=[], worker_instruction='', limitations=[])
    raw = 'Reviewed.\n```json\n' + json.dumps(result) + '\n```'
    rt.import_review(action['id'], raw, 'actual browser response locator')
    assert rt.tick('run')['status'] == 'PROJECT_COMPLETE'
    assert next((rt.root / 'reviews').glob('*.txt')).read_text('utf-8') == raw


def test_malformed_review_does_not_get_invented_or_consume_action(project):
    rt = Runtime(project)
    rt.start('run')
    finish_worker(rt)
    rt.tick('run')
    action = rt.pending()[0]['id']
    rt.claim(action)
    with pytest.raises(ValueError):
        rt.import_review(action, 'Looks good!', 'web message')
    assert rt.store.action(action)['result'] is None


def test_bound_done_worker_must_be_quiescent_before_graph_advances(project):
    rt = Runtime(project)
    rt.start('run')
    # Fixture records a complete real Git candidate; bind separately before fixture claim.
    original = rt.store.bind
    original_claim = rt.claim

    def claim_and_bind(action_id):
        result = original_claim(action_id)
        original(action_id, 'visible-run:B1:A1', 'app thread')
        return result

    rt.claim = claim_and_bind
    action, _ = finish_worker(rt)
    assert rt.tick('run')['status'] == 'WAITING_WORKER'
    rt.observe(action['id'], 'QUIESCENT', 'App finished with no background work', 0)
    assert rt.tick('run')['status'] == 'WAITING_REVIEW'


def test_review_format_correction_is_durable_and_keeps_candidate_budget(project):
    rt = Runtime(project)
    rt.start('run')
    finish_worker(rt)
    rt.tick('run')
    action = rt.pending()[0]
    rt.claim(action['id'])
    rt.bind(action['id'], project.reviewer.url, 'review sent')
    signal = rt.claim_signal(action['id'], 'REVIEW_FORMAT', 0)
    assert Runtime(project).bridge('run')['operation'] == 'RECONCILE_SIGNAL'
    with pytest.raises(ValueError, match='claimed|reconcil'):
        rt.claim_signal(action['id'], 'REVIEW_FORMAT', 0)
    rt.ack_signal(signal['id'], 'format correction message visible in original conversation')
    assert rt.status('run')['counts'] == {'NORMAL': 1, 'ESCALATED': 0}
    assert rt.store.binding(action['id'])['external_id'] == project.reviewer.url


def test_review_schema_restricts_evidence_to_exact_manifest_paths(project):
    from orchestrator.bridge import review_response_schema
    schema = review_response_schema(['result.txt', 'validation.txt'])
    assert schema['properties']['inspected_evidence']['items']['enum'] == [
        'result.txt', 'validation.txt']


def test_sent_review_correction_survives_pause_and_carries_receipt_revision(project):
    rt = Runtime(project)
    rt.start('run')
    finish_worker(rt)
    rt.tick('run')
    action = rt.pending()[0]
    rt.claim(action['id'])
    rt.bind(action['id'], project.reviewer.url, 'review message')
    result = {k: action['payload'][k] for k in
              ['run_id', 'stage_id', 'request_id', 'commit_sha', 'plan_version']}
    result.update(decision='APPROVED', evidence_status='SUFFICIENT',
                  inspected_evidence=['I inspected result.txt'], findings=[],
                  worker_instruction='', limitations=[])
    rt.receipt(action['id'], result, 'bad format response')
    assert rt.tick('run')['status'] == 'BLOCKED'
    signal = rt.claim_signal(action['id'], 'REVIEW_FORMAT', 0)
    rt.ack_signal(signal['id'], 'correction actually sent')
    rt.pause('run', 'pause during correction')
    state = rt.resume('run')
    packet = Runtime(project).bridge('run')
    assert packet['operation'] == 'READ_REVIEW'
    assert packet['receipt_revision'] == 1
    assert packet['blocker']['resume_to'] == 'collect_review'
    with pytest.raises(ValueError, match='already|sent'):
        rt.claim_signal(action['id'], 'REVIEW_FORMAT', state['control']['epoch'])
