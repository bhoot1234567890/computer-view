"""Opt-in real browser observation test using the installed Hermes runtime.

Reads the current authorized default browser only. Does not navigate, type, mint
viewer access, send Telegram messages, or alter the live Computer View lease.
Uses separate temporary Controller state and records only verification metadata.
Run explicitly with the managed unittest runtime, not the standalone test suite.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


class RealObservationTest(unittest.TestCase):
    def test_real_browser_result_provides_fresh_capture_evidence(self):
        from computer_view.control import Controller
        from gateway.session_context import get_session_env
        from tools.browser_use_cli import browser_exec
        with tempfile.TemporaryDirectory(prefix='cv-real-observation-') as directory:
            controller=Controller(directory);controller.initialize();controller.request_handoff()
            result=controller.observe(lambda:browser_exec(
                code='# Verify fresh Computer View observation\nprint(page_info()); print(capture_screenshot())',
                timeout_s=45,task_id=get_session_env('HERMES_SESSION_ID') or None))
            state=controller.status()
            summary=controller._result_data(result)
            evidence={'scope':'actual browser_exec capture with isolated Controller state; not Telegram or full native handoff',
                'passed':not state['needs_observation'] and not state['uncertain'],
                'genuine_result_type':type(result).__name__,
                'result_keys':sorted(result) if isinstance(result,dict) else [],
                'affirmative_success':bool(summary and (summary.get('success') is True or summary.get('ok') is True)),
                'summary_keys':sorted(summary) if isinstance(summary,dict) else [],
                'screenshot_path_present':bool(summary and summary.get('screenshot_path')),
                'output_contains_png':bool(summary and '.png' in str(summary.get('output',''))),
                'fresh_image_file_accepted':not state['needs_observation'],
                'live_lease_modified':False,'desktop_input_sent':False}
            target=ROOT/'artifacts/real-observation.json';target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(evidence,indent=2)+'\n')
            self.assertTrue(evidence['passed'],json.dumps(evidence))


if __name__=='__main__':unittest.main(verbosity=2)
