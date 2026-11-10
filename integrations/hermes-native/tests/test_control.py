import pytest


def test_settled_same_viewer_takeover_preserves_the_entire_lease(tmp_path):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    expected = c.takeover('same', reason='Original lease reason')
    persisted = c.path.read_bytes()
    for _ in range(3):
        assert Controller(tmp_path).takeover('same', reason='Must not replace reason') == expected
        assert c.path.read_bytes() == persisted
    assert c.may_input('same')


@pytest.mark.parametrize('marker', ['in_flight', 'uncertain', 'handback_pending'])
@pytest.mark.parametrize('viewer', ['same', 'different'])
def test_repeated_takeover_refuses_unsettled_lease(tmp_path, marker, viewer):
    from computer_view.control import Controller, Busy
    c = Controller(tmp_path); c.initialize(); c.takeover('same')
    data = c._read(); data[marker] = True; c._write(data)
    persisted = c.path.read_bytes()
    with pytest.raises(Busy): c.takeover(viewer)
    assert c.path.read_bytes() == persisted


@pytest.mark.parametrize('end_lease', ['handback', 'recover'])
@pytest.mark.parametrize('viewer', ['same', 'different'])
def test_real_new_takeover_advances_epoch(tmp_path, end_lease, viewer):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    original = c.takeover('same')
    if end_lease == 'handback': c.handback('same')
    else: c.recover()
    previous = c.status()
    current = c.takeover(viewer)
    assert current['epoch'] == previous['epoch'] + 1 > original['epoch']
    assert len(current['events']) == len(previous['events']) + 1
    assert current['mine'] and c.may_input(viewer)


@pytest.mark.parametrize('new_viewer', ['same', 'different'])
def test_late_cleanup_failure_cannot_poison_a_new_lease(tmp_path, new_viewer):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize(); c.takeover('same')
    old_epoch = c.status()['epoch']
    c.recover(); c.takeover(new_viewer)
    expected = c.status(new_viewer)
    c.release_failed('same', old_epoch)
    assert c.status(new_viewer) == expected
    assert c.may_input(new_viewer)


def test_cleanup_without_an_admitted_epoch_cannot_poison_current_lease(tmp_path):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize(); c.takeover('viewer')
    expected = c.status('viewer')
    c.release_failed('viewer', None)
    assert c.status('viewer') == expected
    assert c.may_input('viewer')


# Explicit Hermes-contract test doubles, not live captures/provider responses.
# A one-pixel PNG used only as isolated on-disk test data.
TEST_PNG = __import__('base64').b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==')


def computer_capture_fixture(mode='ax', native=False, action=None):
    """Explicit, isolated contract double; never a productive desktop capture.

    Source: tools/computer_use/tool.py _text_capture_payload (683-692),
    _capture_response (698-740), _maybe_follow_capture (742-763), and
    backend.py CaptureResult. Native summaries are prose, not success JSON.
    """
    import base64
    import json
    # 8x8 isolated PNG meets the core's native-provider minimum (unlike TEST_PNG).
    image = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAEklEQVR4nGP4z8DwHx9mGBkKAMLXf4EvceABAAAAAElFTkSuQmCC')
    summary = f'capture mode={mode} 8x8\n0 interactable element(s):'
    if not native:
        result = {'mode': mode, 'width': 8, 'height': 8, 'app': '',
                  'window_title': '', 'elements': [], 'total_elements': 0,
                  'summary': summary}
        if action is not None:
            result.update(action)
        return json.dumps(result)
    result = {'_multimodal': True, 'text_summary': summary,
              'meta': {'mode': mode, 'width': 8, 'height': 8,
                       'elements': 0, 'png_bytes': len(image)},
              'content': [{'type': 'text', 'text': summary},
                          {'type': 'image_url', 'image_url': {'url':
                           'data:image/png;base64,' + base64.b64encode(image).decode()}}]}
    if action is not None:
        result['action_result'] = action
        result['text_summary'] = json.dumps(action) + '\n\n' + summary
        result['content'][0]['text'] = result['text_summary']
    return result


@pytest.mark.parametrize('mode', ['ax', 'som', 'vision'])
def test_computer_use_text_capture_settles_without_invented_success_flag(tmp_path, mode):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = computer_capture_fixture(mode)
    assert c.execute('computer_use', {}, lambda args: result) is result
    assert not c.status()['uncertain']
    assert c.takeover('human')['mine']


@pytest.mark.parametrize('action', [None, {'ok': True, 'action': 'click',
    'verified': False, 'effect': 'unverifiable', 'verdict': 'unverified'}])
@pytest.mark.parametrize('mode', ['som', 'vision'])
def test_computer_use_native_capture_and_capture_after_settle(tmp_path, mode, action):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = computer_capture_fixture(mode, native=True, action=action)
    assert c.execute('computer_use', {}, lambda args: result) is result
    assert not c.status()['uncertain']
    assert c.takeover('human')['mine']


@pytest.mark.parametrize('native,action', [(False, None), (True, None),
    (True, {'ok': True, 'action': 'click', 'verdict': 'unverified'})])
@pytest.mark.parametrize('operation', ['execute', 'observe'])
def test_computer_capture_contract_never_satisfies_browser_observation(tmp_path, native, action, operation):
    import json
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize(); c.takeover('v'); c.handback('v')
    def capture():
        result = computer_capture_fixture('som', native=native, action=action)
        path = tmp_path / 'fresh.png'; path.write_bytes(TEST_PNG)
        if native:
            result['meta']['screenshot_path'] = str(path)
        else:
            result = json.loads(result); result['screenshot_path'] = str(path)
            result = json.dumps(result)
        return result
    if operation == 'observe':
        c.observe(capture)
        assert c.status()['needs_observation']
    else:
        c.recover()
        c.observe(lambda: capture_fixture(tmp_path / 'browser.png'))
        c.execute('browser_exec', {}, lambda args: capture())
    assert c.status()['uncertain']


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('outer', [{'error': 'failed'}, {'isError': True},
    {'ok': False}, {'success': False}, {'exit_code': 1}, {'status': 'error'}])
def test_computer_capture_outer_failure_is_never_hidden(tmp_path, native, outer):
    import json
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = computer_capture_fixture('som', native=native)
    if not native: result = json.loads(result)
    result.update(outer)
    assert c.execute('computer_use', {}, lambda args: result) is result
    assert c.status()['uncertain']


@pytest.mark.parametrize('change', [
    {'mode': 'unknown'}, {'width': 0}, {'height': True}, {'elements': [None]},
    {'elements': [{}], 'total_elements': 1}, {'total_elements': -1},
    {'summary': ''}, {'app': None}, {'window_title': []},
    {'summary': 'capture mode=ax 8x8garbage\n0 interactable element(s):'},
    {'_multimodal': False, 'ok': True},
])
def test_malformed_computer_text_capture_is_uncertain(tmp_path, change):
    import json
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = json.loads(computer_capture_fixture()); result.update(change)
    c.execute('computer_use', {}, lambda args: result)
    assert c.status()['uncertain']


@pytest.mark.parametrize('change', [
    {'meta': None}, {'meta': {}}, {'meta': {'width': 0}},
    {'text_summary': 'unknown'}, {'content': []}, {'_multimodal': False, 'ok': True},
    {'action_result': {'ok': False, 'action': 'click'}},
    {'action_result': {'ok': 'true', 'action': 'click'}},
    {'action_result': {'ok': True, 'error': 'failed'}},
    {'action_result': {'ok': True, 'action': 'different'}},
])
def test_malformed_computer_native_capture_is_uncertain(tmp_path, change):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = computer_capture_fixture('som', native=True,
        action={'ok': True, 'action': 'click', 'verdict': 'unverified'})
    result.update(change)
    c.execute('computer_use', {}, lambda args: result)
    assert c.status()['uncertain']


@pytest.mark.parametrize('tool_name,result,failed', [
    # tool.py _text_response/_action_payload: ok is transport, not effect verification.
    ('computer_use', '{"ok":true,"action":"click","verified":false,"effect":"unverifiable"}', False),
    ('computer_use', '{"ok":false,"action":"click"}', True),
    # browser_vault_tool.py: list 297, unlock 334, enter_code 446, _handle_vault 682.
    ('browser_vault', '{"success":true,"items":[]}', False),
    ('browser_vault', '{"success":true,"filled_fields":1,"origin":"https://example.test","source":"local"}', False),
    ('browser_vault', '{"success":false,"filled_fields":0}', True),
    ('browser_vault', '{"success":true,"error":"failed"}', True),
    ('browser_vault', '{"status":"ok"}', True),
    ('computer_use', '{"status":"ok"}', True),
    ('computer_use', 'capture mode=ax 1024x768', True),
])
def test_source_backed_action_and_vault_results(tmp_path, tool_name, result, failed):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    assert c.execute(tool_name, {}, lambda args: result) is result
    assert c.status()['uncertain'] is failed


@pytest.mark.parametrize('mode', ['ax', 'som', 'vision'])
@pytest.mark.parametrize('ok', [False, True])
def test_computer_text_capture_after_keeps_action_transport_verdict(tmp_path, mode, ok):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = computer_capture_fixture(mode, action={'ok': ok, 'action': 'click',
        'verified': False, 'effect': 'unverifiable', 'verdict': 'unverified'})
    assert c.execute('computer_use', {}, lambda args: result) is result
    assert c.status()['uncertain'] is (not ok)


@pytest.mark.parametrize('marker', [
    {'screen_unchanged': True}, {'vision_unavailable': True},
    {'vision_analysis': 'Explicit isolated analysis fixture',
     'vision_analysis_routed_via': 'auxiliary.vision', 'width': 16, 'height': 16},
])
def test_source_backed_text_capture_branches(tmp_path, marker):
    # tool.py 703-740 and 963-967: aux metadata uses backend dimensions,
    # while the human summary can describe the downscaled inline image.
    import json
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = json.loads(computer_capture_fixture('som')); result.update(marker)
    c.execute('computer_use', {}, lambda args: result)
    assert not c.status()['uncertain']


@pytest.mark.parametrize('element', [{}, {'index': True, 'role': 'button', 'label': '',
    'bounds': None, 'app': ''}, {'index': 1, 'role': 'button', 'label': '',
    'bounds': 'unknown', 'app': ''}])
def test_computer_capture_rejects_malformed_element_records(tmp_path, element):
    import json
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = json.loads(computer_capture_fixture())
    result.update(elements=[element], total_elements=1,
                  summary='capture mode=ax 8x8\n1 interactable element(s):')
    c.execute('computer_use', {}, lambda args: result)
    assert c.status()['uncertain']


@pytest.mark.parametrize('bounds', [None, [-10, 0, 40, 20], [0, 0, 0, 0]])
def test_computer_capture_accepts_source_element_geometry(tmp_path, bounds):
    import json
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = json.loads(computer_capture_fixture())
    result.update(elements=[{'index': 1, 'role': 'button', 'label': 'Accept',
        'bounds': bounds, 'app': 'test'}], total_elements=1,
        summary='capture mode=ax 8x8\n1 interactable element(s):')
    c.execute('computer_use', {}, lambda args: result)
    assert not c.status()['uncertain']


@pytest.mark.parametrize('change', [
    {'index': 0}, {'role': None}, {'label': []}, {'app': None},
    {'bounds': [0, 0, True, 4]}, {'bounds': [0, 0, -1, 4]},
    {'bounds': [0, 1]},
])
def test_computer_capture_rejects_invalid_source_element_fields(tmp_path, change):
    import json
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    element = {'index': 1, 'role': 'button', 'label': '', 'app': '', 'bounds': None}
    element.update(change)
    result = json.loads(computer_capture_fixture())
    result.update(elements=[element], total_elements=1,
        summary='capture mode=ax 8x8\n1 interactable element(s):')
    c.execute('computer_use', {}, lambda args: result)
    assert c.status()['uncertain']


@pytest.mark.parametrize('change', [
    {'vision_analysis': ''}, {'vision_analysis': True},
    {'vision_analysis_routed_via': 'other'},
    {'summary': 'capture mode=som invalid\n0 interactable element(s):'},
])
def test_auxiliary_metadata_does_not_permit_malformed_capture(tmp_path, change):
    import json
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = json.loads(computer_capture_fixture('som'))
    result.update(width=16, height=16, vision_analysis='explicit isolated fixture',
                  vision_analysis_routed_via='auxiliary.vision')
    result.update(change)
    c.execute('computer_use', {}, lambda args: result)
    assert c.status()['uncertain']


def capture_fixture(path, native=False):
    import json
    path.write_bytes(TEST_PNG)
    summary = {'success': True, 'exit_code': 0, 'output': 'explicit capture fixture',
               'screenshot_path': str(path)}
    return native_browser_fixture(summary) if native else json.dumps(summary)


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('evidence', ['missing', 'stale', 'empty', 'not-image', 'fresh'])
def test_observe_requires_fresh_image_file_evidence(tmp_path, native, evidence):
    import json, os
    from computer_view.control import Controller, Busy
    c = Controller(tmp_path); c.initialize(); c.takeover('v'); c.handback('v')
    path = tmp_path / 'fixture.png'
    path.write_bytes(TEST_PNG); os.utime(path, (1, 1))
    def capture():
        summary = {'success': True, 'exit_code': 0, 'output': 'explicit fixture'}
        if evidence != 'missing': summary['screenshot_path'] = str(path)
        if evidence == 'fresh': path.write_bytes(TEST_PNG)
        if evidence == 'empty': path.write_bytes(b'')
        if evidence == 'not-image': path.write_bytes(b'explicit non-image fixture')
        return native_browser_fixture(summary) if native else json.dumps(summary)
    returned = []
    def call():
        result = capture(); returned.append(result); return result
    assert c.observe(call) == returned[0]
    assert c.status()['needs_observation'] is (evidence != 'fresh')
    assert c.status()['uncertain'] is (evidence != 'fresh')
    if evidence != 'fresh':
        with pytest.raises(Busy): c.takeover('v')
        assert 'refused' in c.execute('browser_exec', {}, lambda args: pytest.fail('fenced'))


@pytest.mark.parametrize('result', ['result', 'not json', '[]', 'null', '{}',
    '{"success":"true"}', '{"success":true,"exit_code":1}',
    {'_multimodal': True, 'content': [], 'text_summary': 'malformed'},
    {'_multimodal': True, 'content': [], 'text_summary': '[]'},
    {'_multimodal': True, 'content': [], 'text_summary': '{"success":true,"error":"failed"}'}])
def test_unknown_or_malformed_results_fail_closed(tmp_path, result):
    from computer_view.control import Controller, Busy
    c = Controller(tmp_path); c.initialize()
    assert c.execute('browser_exec', {}, lambda args: result) == result
    assert c.status()['uncertain']
    with pytest.raises(Busy): c.takeover('v')
def native_browser_fixture(summary):
    import base64
    import json
    return {'_multimodal': True, 'text_summary': json.dumps(summary),
            'meta': {'screenshot_path': summary.get('screenshot_path'), 'native_vision': True},
            'content': [{'type': 'text', 'text': json.dumps(summary)},
                        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(TEST_PNG).decode()}}]}


@pytest.mark.parametrize('content', [[], [None], [{'type': 'image_url', 'image_url': {'url': 'not-image'}}]])
def test_malformed_native_content_is_not_success(tmp_path, content):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = native_browser_fixture({'success': True, 'exit_code': 0, 'output': 'explicit fixture'})
    result['content'] = content
    assert c.execute('browser_exec', {}, lambda args: result) is result
    assert c.status()['uncertain']


def test_observation_exception_without_prior_handoff_still_requires_observation(tmp_path):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    def failed(): raise OSError('explicit capture exception fixture')
    assert 'error' in c.observe(failed)
    assert c.status()['needs_observation'] and c.status()['uncertain']


@pytest.mark.parametrize('outer', [
    {'success': False}, {'ok': False}, {'exit_code': 1}, {'status': 'error'}])
def test_native_outer_failure_cannot_be_hidden_by_success_summary(tmp_path, outer):
    from computer_view.control import Controller
    c = Controller(tmp_path); c.initialize()
    result = native_browser_fixture({'success': True, 'exit_code': 0, 'output': 'explicit fixture'})
    result.update(outer)
    assert c.execute('browser_exec', {}, lambda args: result) is result
    assert c.status()['uncertain']


@pytest.mark.parametrize('operation', ['execute', 'observe'])
def test_native_browser_failure_is_uncertain_even_with_attached_image(tmp_path, operation):
    from computer_view.control import Controller, Busy
    c = Controller(tmp_path); c.initialize()
    if operation == 'observe':
        c.takeover('v'); c.handback('v')
    failure = native_browser_fixture({'success': False, 'exit_code': 1,
        'output': 'explicit failure fixture', 'screenshot_path': '/nonexistent/fixture.png'})
    result = (c.observe(lambda: failure) if operation == 'observe' else
              c.execute('browser_exec', {}, lambda args: failure))
    assert result is failure
    assert c.status()['uncertain']
    if operation == 'observe': assert c.status()['needs_observation']
    with pytest.raises(Busy): c.takeover('v')


def test_takeover_blocks_agent_and_handback_requires_new_observation(tmp_path):
    from computer_view.control import Controller
    c = Controller(tmp_path)
    c.initialize()
    ran = []
    run = lambda args: ran.append(args) or capture_fixture(tmp_path / 'test-capture.png')
    assert 'explicit capture fixture' in c.execute('browser_exec', {'code':'click'}, run)
    assert c.takeover('viewer-one')['mine']
    denied = c.execute('browser_exec', {'code':'click'}, run)
    assert 'human' in denied.lower()
    assert len(ran) == 1
    assert c.handback('viewer-one')['needs_observation']
    assert 'observation' in c.execute('browser_exec', {}, run).lower()
    assert len(ran) == 1
    assert 'explicit capture fixture' in c.observe(lambda: c.execute('browser_exec', {'code':'capture'}, run))
    assert 'explicit capture fixture' in c.execute('computer_use', {'action':'click'}, run)
    assert len(ran) == 3


def test_failed_observation_keeps_input_fenced(tmp_path):
    from computer_view.control import Controller
    for index, failure in enumerate(['{"success":false,"output":"Browser disconnected"}',
                    '{"isError":true}', '{"status":"error"}', '{"error":"failed"}']):
        c = Controller(tmp_path / str(index)); c.initialize()
        c.takeover('v'); c.handback('v')
        assert c.observe(lambda: failure) == failure
        assert c.status()['needs_observation']
        assert c.status()['uncertain']


def test_other_viewer_cannot_release_or_co_drive(tmp_path):
    from computer_view.control import Controller, Busy
    c=Controller(tmp_path); c.initialize(); c.takeover('first')
    with pytest.raises(PermissionError): c.handback('other')
    with pytest.raises(Busy): c.takeover('other')
    assert not c.may_input('other')
    assert c.may_input('first')


def test_missing_or_corrupt_state_denies_screen_tools_not_unrelated_tools(tmp_path):
    from computer_view.control import Controller
    c=Controller(tmp_path); ran=[]
    callback=lambda args: ran.append(args) or 'ok'
    assert 'refused' in c.execute('browser_exec',{},callback)
    assert not ran
    c.initialize(); c.path.write_text('{broken')
    assert 'refused' in c.execute('computer_use',{},callback)
    assert not c.may_input('any')
    assert c.execute('web_search',{},callback)=='ok'


def test_takeover_does_not_interrupt_inflight_admitted_tool(tmp_path):
    import threading
    from computer_view.control import Controller, Busy
    c=Controller(tmp_path); c.initialize()
    entered=threading.Event(); finish=threading.Event(); output=[]
    def downstream(args):
        entered.set(); assert finish.wait(3); return '{"success":true,"exit_code":0,"output":"explicit completion fixture"}'
    t=threading.Thread(target=lambda: output.append(c.execute('browser_exec',{},downstream)))
    t.start()
    try:
        assert entered.wait(2)
        with pytest.raises(Busy): Controller(tmp_path).takeover('human')
        assert c.status()['holder']=='agent'
    finally:
        finish.set(); t.join(3)
    assert output==['{"success":true,"exit_code":0,"output":"explicit completion fixture"}']
    assert c.takeover('human')['mine']


def test_control_and_viewer_id_do_not_leak_in_status(tmp_path):
    import stat
    from computer_view.control import Controller
    c=Controller(tmp_path); c.initialize(); c.takeover('private-viewer-capability')
    assert 'private-viewer-capability' not in str(c.status())
    assert stat.S_IMODE(c.path.stat().st_mode)==0o600
    assert stat.S_IMODE(c.lockpath.stat().st_mode)==0o600


def test_viewer_frame_uses_same_fence_and_owner_recovery_needs_observation(tmp_path):
    from computer_view.control import Controller
    c=Controller(tmp_path); c.initialize()
    assert c.frame('v',lambda allowed: allowed) is False
    c.takeover('v')
    assert c.frame('v',lambda allowed: allowed) is True
    assert c.frame('other',lambda allowed: allowed) is False
    with c._lock():
        assert c.frame('v',lambda allowed: allowed) is False
    assert c.recover()['needs_observation']
    assert c.status()['holder']=='agent'
    assert c.frame('v',lambda allowed: allowed) is False


def test_handback_releases_inputs_before_agent_is_admitted(tmp_path):
    from computer_view.control import Controller
    c=Controller(tmp_path); c.initialize(); c.takeover('v')
    seen=[]
    c.handback('v', before_release=lambda: seen.append(c.status()['holder']))
    assert seen==['human']
    assert c.status()['holder']=='agent'


def test_failed_or_timed_out_agent_operation_prevents_takeover_until_owner_recovery(tmp_path):
    from computer_view.control import Controller, Busy
    c=Controller(tmp_path); c.initialize()
    failure='{"success":false,"error":"Timed out; browser daemon may still be working"}'
    assert c.execute('browser_exec',{},lambda args: failure)==failure
    assert c.status()['uncertain']
    with pytest.raises(Busy): c.takeover('human')
    assert 'refused' in c.execute('computer_use',{},lambda args:'must-not-run')
    assert 'error' in c.observe(lambda:'must-not-capture')
    c.recover()
    assert not c.status()['uncertain']
    assert c.status()['needs_observation']


def test_observation_exception_marks_desktop_uncertain(tmp_path):
    from computer_view.control import Controller, Busy
    c=Controller(tmp_path); c.initialize(); c.takeover('human'); c.handback('human')
    def failed_capture(): raise RuntimeError('browser disconnected during capture')
    assert 'error' in c.observe(failed_capture)
    assert c.status()['uncertain']
    assert c.status()['needs_observation']
    with pytest.raises(Busy): c.takeover('human')


def test_request_handoff_records_request_without_granting_human_input(tmp_path):
    from computer_view.control import Controller
    c=Controller(tmp_path); c.initialize()
    state=c.request_handoff()
    assert state['holder']=='agent' and not state['mine']
    assert state['needs_observation']
    assert 'Human assistance requested' in state['reason']
    assert not c.may_input('any')


def test_stale_handback_completion_cannot_release_a_newer_lease(tmp_path):
    from computer_view.control import Controller, Busy
    c = Controller(tmp_path); c.initialize(); c.takeover('old')
    token = c.begin_handback('old')
    assert c.status()['holder'] == 'human' and c.status()['in_flight']
    assert not c.may_input('old')
    c.recover(); c.takeover('new')
    with pytest.raises(Busy): c.finish_handback('old', token)
    with pytest.raises(Busy): c.fail_handback('old', token)
    assert c.status('new')['mine'] and c.may_input('new')
    assert not c.status()['uncertain']


def test_release_write_failure_keeps_human_uncertain_until_recovery(tmp_path):
    from computer_view.control import Controller, Busy
    c = Controller(tmp_path); c.initialize(); c.takeover('v')
    def fail(): raise OSError('explicit writer failure fixture')
    with pytest.raises(OSError): c.begin_handback('v', before_release=fail)
    assert c.status()['holder'] == 'human' and c.status()['uncertain']
    assert not c.may_input('v')
    with pytest.raises(Busy): c.handback('v')


def test_persisted_inflight_marker_survives_agent_process_death(tmp_path):
    import subprocess, sys, time
    from computer_view.control import Controller, Busy
    c=Controller(tmp_path); c.initialize()
    code='''from computer_view.control import Controller
import sys,time,pathlib
c=Controller(sys.argv[1])
def work(args):
    pathlib.Path(sys.argv[1], 'entered').touch()
    time.sleep(60)
c.execute('browser_exec',{},work)
'''
    p=subprocess.Popen([sys.executable,'-c',code,str(tmp_path)])
    try:
        deadline=time.monotonic()+3
        while not (tmp_path/'entered').exists() and time.monotonic()<deadline: time.sleep(.02)
        assert (tmp_path/'entered').exists()
        p.kill(); p.wait(timeout=3)
        with pytest.raises(Busy): c.takeover('human')
        assert c.status()['in_flight']
    finally:
        if p.poll() is None: p.kill(); p.wait()
