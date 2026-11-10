"""Installed-Hermes contract tests; run with its managed unittest runtime.

Registers the real candidate through PluginContext and runs Hermes's actual
middleware/model_tools entrypoints. Device/observation execution is explicitly
spied or mocked: this is NOT a live desktop or Telegram demonstration.
"""
import contextvars
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


class InstalledRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('cv_native_test',ROOT/'__init__.py',
            submodule_search_locations=[str(ROOT)])
        cls.plugin=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=cls.plugin
        spec.loader.exec_module(cls.plugin)

    def setUp(self):
        from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
        from hermes_constants import set_hermes_home_override
        from gateway.session_context import set_session_vars
        from computer_view.config import Settings
        from computer_view.control import Controller
        from plugins.plugin_storage import plugin_data_dir
        self.directory=tempfile.TemporaryDirectory(prefix='cv-native-test-')
        self.addCleanup(self.directory.cleanup)
        self.home=Path(self.directory.name)
        self.home_token=set_hermes_home_override(self.home)
        from hermes_constants import reset_hermes_home_override
        self.addCleanup(reset_hermes_home_override,self.home_token)
        # Disposable test profile configuration, never the owner's config.
        (self.home/'config.yaml').write_text('plugins:\n  isolation: in_process\n')
        self.identity=set_session_vars(platform='telegram',user_id='42',chat_id='42',
            chat_type='dm',profile='default')
        from gateway.session_context import clear_session_vars
        self.addCleanup(clear_session_vars,self.identity)
        self.data=plugin_data_dir('computer-view');self.data.chmod(0o700)
        settings=Settings.from_dict(dict(owner_id='42',profile='default',owner_uid=os.getuid(),
            origin='https://cv-native-test.invalid',rfb_socket=str(self.home/'rfb.sock'),
            novnc_dir=str(self.home/'novnc')))
        p=self.data/'settings.json';p.write_text(json.dumps(settings.as_dict()));p.chmod(0o600)
        self.control=Controller(self.data);self.control.initialize()
        # Tool registrations are profile overlays: register AFTER entering the scratch profile.
        self.manager=PluginManager()
        self.ctx=PluginContext(PluginManifest(name='computer-view',path=str(ROOT)),self.manager)
        self.plugin.register(self.ctx)
        self.delivery=patch('hermes_cli.plugins._delivery_manager',return_value=self.manager)
        self.delivery.start();self.addCleanup(self.delivery.stop)

    def test_real_core_middleware_denies_human_control_and_corrupt_state(self):
        from hermes_cli.middleware import run_tool_execution_middleware
        calls=[]
        self.control.takeover('fixture-viewer')
        result=run_tool_execution_middleware('browser_exec',{'code':'capture_screenshot()'},
            lambda args:calls.append(args))
        self.assertIn('Human',result);self.assertEqual(calls,[])
        self.control.path.write_text('{corrupt')
        result=run_tool_execution_middleware('computer_use',{'action':'capture'},lambda args:calls.append(args))
        self.assertIn('refused',result);self.assertEqual(calls,[])

    def test_actual_model_tools_entrypoint_hits_guard_before_registry_dispatch(self):
        from model_tools import handle_function_call
        self.control.takeover('fixture-viewer')
        with patch('model_tools.registry.dispatch') as device:
            for name,args in [('computer_use',{'action':'capture'}),('browser_exec',{'code':'print(1)'}),
                              ('browser_vault',{'action':'list'})]:
                result=handle_function_call(name,args,task_id='cv-fixture',session_id='cv-fixture',
                    skip_pre_tool_call_hook=True,skip_tool_request_middleware=True)
                self.assertIn('Human',str(result))
            device.assert_not_called()

    def test_real_middleware_holds_lock_until_next_call_finishes(self):
        from hermes_cli.middleware import run_tool_execution_middleware
        from computer_view.control import Busy
        entered=threading.Event();finish=threading.Event();result=[]
        def device(args):
            entered.set();self.assertTrue(finish.wait(3));return json.dumps({'success':True})
        context=contextvars.copy_context()
        thread=threading.Thread(target=lambda:context.run(lambda:result.append(
            run_tool_execution_middleware('browser_exec',{},device))))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(Busy):self.control.takeover('fixture-viewer')
        finally:
            finish.set();thread.join(3)
        self.assertEqual(result,[json.dumps({'success':True})])
        self.assertTrue(self.control.takeover('fixture-viewer')['mine'])

    def test_real_registration_handoff_and_fixed_observation_tool(self):
        from tools.registry import registry
        handoff=registry.dispatch('computer_view_handoff',{})
        self.assertEqual(json.loads(handoff),{'ok':True,'status':'handoff_requested'})
        self.assertTrue(self.control.status()['needs_observation'])
        self.control.takeover('fixture-viewer');self.control.handback('fixture-viewer')
        def fixture_capture(*args, **kwargs):
            # Explicit test data written DURING capture, not a live screenshot.
            import base64
            path = self.home / 'explicit-fixture-capture.png'
            path.write_bytes(base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=='))
            return json.dumps({'success': True, 'exit_code': 0, 'output': 'explicit fixture observation',
                               'screenshot_path': str(path)})
        with patch.object(self.ctx,'dispatch_tool',side_effect=fixture_capture) as observe:
            result=registry.dispatch('computer_view_observe',{},task_id='cv-observe-task',session_id='cv-observe-session')
            self.assertTrue(json.loads(result)['success'])
            args,kwargs=observe.call_args
            self.assertEqual(args[0],'browser_exec')
            self.assertEqual(args[1],{'code':'# Observe Computer View after handback\nprint(page_info()); print(capture_screenshot())'})
            self.assertEqual(kwargs,{'task_id':'cv-observe-task','session_id':'cv-observe-session'})
        self.assertFalse(self.control.status()['needs_observation'])

    def test_actual_task_local_identity_denies_different_private_user(self):
        from gateway.session_context import set_session_vars,clear_session_vars
        from tools.registry import registry
        token=set_session_vars(platform='telegram',user_id='99',chat_id='99',chat_type='dm',profile='default')
        try:
            result=registry.dispatch('computer_view_status',{})
            self.assertEqual(json.loads(result)['error'],'owner_private_session_required')
        finally:clear_session_vars(token)


if __name__=='__main__':unittest.main(verbosity=2)
