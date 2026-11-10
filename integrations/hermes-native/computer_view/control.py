"""Profile-wide, cross-process input fence using public Hermes execution middleware.

Not an OS isolation boundary. Raw CDP, shell commands and other same-user clients
are outside this fence. An admitted tool completes before takeover is granted.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import base64
import fcntl
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

SCREEN_TOOLS = frozenset({'browser_exec', 'computer_use', 'browser_vault'})
_OBSERVING = ContextVar('computer_view_observing', default=None)


class Busy(RuntimeError):
    """A supported agent tool is finishing; takeover has not been granted."""


class Controller:
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve()
        self.path = self.root / 'control.json'
        self.lockpath = self.root / 'control.lock'

    @contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Busy('A desktop action is still finishing. Try takeover again shortly.') from None
            yield
        finally:
            os.close(fd)

    def initialize(self):
        with self._lock():
            if self.path.exists() or self.path.is_symlink():
                self._read()
                return
            self._write({'holder':'agent', 'viewer_id':None, 'epoch':0,
                         'needs_observation':False, 'in_flight':False, 'uncertain':False,
                         'reason':'Agent control', 'events':[]})

    def _read(self):
        if self.path.is_symlink():
            raise ValueError('Unsafe control state')
        data = json.loads(self.path.read_text())
        if (not isinstance(data, dict) or data.get('holder') not in ('agent','human') or
                not isinstance(data.get('epoch'), int) or
                not isinstance(data.get('needs_observation'), bool) or
                not isinstance(data.get('in_flight'), bool) or
                not isinstance(data.get('uncertain'), bool) or
                (data['holder']=='human' and not isinstance(data.get('viewer_id'), str))):
            raise ValueError('Invalid control state')
        return data

    def _write(self, data):
        fd, name = tempfile.mkstemp(prefix='.control-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump(data, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _event(self, data, event):
        data['events'] = (data.get('events', []) + [{'at':time.time(),'event':event}])[-30:]
        data['epoch'] += 1
        self._write(data)

    def status(self, viewer_id=''):
        data = self._read()
        return {'holder':data['holder'], 'mine':bool(viewer_id and data.get('viewer_id') and
                hmac.compare_digest(viewer_id, data['viewer_id'])),
                'epoch':data['epoch'], 'needs_observation':data['needs_observation'],
                'in_flight':data['in_flight'], 'uncertain':data['uncertain'],
                'reason':data.get('reason',''), 'events':data.get('events',[])}

    def request_handoff(self):
        """Ask for assistance, without granting any viewer input authority."""
        with self._lock():
            data = self._read()
            if data['in_flight'] or data['uncertain']:
                raise Busy('A desktop action has not settled')
            if data['holder'] == 'agent':
                data.update(needs_observation=True,
                    reason='Human assistance requested. Open Computer View and choose Take control.')
                self._event(data, 'Human assistance requested')
        return self.status()

    def takeover(self, viewer_id, reason='Human took control'):
        if not isinstance(viewer_id, str) or not viewer_id:
            raise ValueError('Viewer identity required')
        with self._lock():
            data = self._read()
            if data['in_flight'] or data['uncertain'] or data.get('handback_pending', False):
                raise Busy('A prior desktop action has not settled. Owner recovery is required.')
            if data['holder']=='human':
                if data.get('viewer_id') != viewer_id:
                    raise Busy('Another viewer holds control')
                # This is the existing lease; preserve admitted input's epoch.
                return self.status(viewer_id)
            data.update(holder='human', viewer_id=viewer_id, needs_observation=True, reason=reason[:200])
            self._event(data, 'Human took control')
        return self.status(viewer_id)

    def handback(self, viewer_id, *, before_release=None):
        """Synchronous convenience; network callers must drain between phases."""
        token = self.begin_handback(viewer_id, before_release=before_release)
        return self.finish_handback(viewer_id, token)

    def begin_handback(self, viewer_id, *, before_release=None):
        with self._lock():
            data = self._read()
            if data['holder']!='human' or data.get('viewer_id') != viewer_id:
                raise PermissionError('Only the current viewer may hand back control')
            if data['in_flight'] or data['uncertain']:
                raise Busy('Input releases have not settled; owner recovery required')
            data.update(in_flight=True, handback_pending=True, needs_observation=True,
                        reason='Handback pending; input releases are draining')
            self._event(data, 'Handback pending')
            try:
                if before_release is not None:
                    before_release()
            except BaseException:
                data.update(in_flight=False, handback_pending=False, uncertain=True,
                            reason='Input release failed; owner recovery required')
                self._event(data, 'Input release failed')
                raise
            return data['epoch']

    def finish_handback(self, viewer_id, token):
        with self._lock():
            data = self._read()
            self._check_handback(data, viewer_id, token)
            data.update(holder='agent', viewer_id=None, needs_observation=True,
                        in_flight=False, handback_pending=False,
                        reason='Handed back. A fresh observation is required before agent input.')
            self._event(data, 'Handed back to Hermes; observation required')
        return self.status(viewer_id)

    @staticmethod
    def _check_handback(data, viewer_id, token):
        if (data['holder'] != 'human' or data.get('viewer_id') != viewer_id or
                data['epoch'] != token or not data.get('handback_pending') or
                not data['in_flight'] or data['uncertain']):
            raise Busy('Handback no longer current; operation refused')

    def fail_handback(self, viewer_id, token):
        with self._lock():
            data = self._read()
            self._check_handback(data, viewer_id, token)
            data.update(in_flight=False, handback_pending=False, uncertain=True,
                        needs_observation=True, reason='Input release failed; owner recovery required')
            self._event(data, 'Input release failed')

    def release_failed(self, viewer_id, epoch):
        """Fence failed cleanup only for the lease that admitted its input."""
        with self._lock():
            data = self._read()
            if (data['holder'] == 'human' and data.get('viewer_id') == viewer_id and
                    data['epoch'] == epoch):
                data.update(in_flight=False, handback_pending=False, uncertain=True,
                            needs_observation=True, reason='Input release failed; owner recovery required')
                self._event(data, 'Input release failed')

    def may_input(self, viewer_id):
        try:
            data = self._read()
            return (data['holder']=='human' and not data['in_flight'] and not data['uncertain'] and
                    not data.get('handback_pending', False) and
                    hmac.compare_digest(data.get('viewer_id',''), viewer_id))
        except Exception:
            return False

    def frame(self, viewer_id, forward):
        """Parse and write one viewer frame under the agent admission lock.

        forward(allowed) must be synchronous: parse buffered RFB messages then
        writer.write(bytes). Await drain only AFTER this method returns.
        """
        try:
            with self._lock():
                return forward(self.may_input(viewer_id))
        except Busy:
            # An agent operation may run while watching. Never permit input.
            return forward(False)

    def recover(self):
        """Explicit owner-local recovery after a lost/expired human session."""
        with self._lock():
            data = self._read()
            data.update(holder='agent', viewer_id=None, needs_observation=True,
                        in_flight=False, uncertain=False, handback_pending=False,
                        reason='Owner recovered control; fresh observation required')
            self._event(data, 'Owner recovered control; observation required')
        return self.status()

    def execute(self, tool_name, args, next_call, **kwargs):
        if tool_name not in SCREEN_TOOLS:
            return next_call(args)
        if _OBSERVING.get() == str(self.root):
            return next_call(args)
        try:
            with self._lock():
                data = self._read()
                if data['holder']=='human':
                    return json.dumps({'error':'Human has control. Do not act or capture until explicit handback.'})
                if data['in_flight'] or data['uncertain']:
                    return json.dumps({'error':'Prior desktop operation unsettled; operation refused. Owner recovery required.'})
                if data['needs_observation']:
                    return json.dumps({'error':'Fresh observation required. Call computer_view_observe before continuing.'})
                data.update(in_flight=True, reason='A desktop action is in flight')
                self._write(data)
                try:
                    result = next_call(args)
                except BaseException:
                    data.update(in_flight=False, uncertain=True,
                                reason='Desktop action did not settle; owner must verify and recover')
                    self._write(data)
                    raise
                data.update(in_flight=False, uncertain=self._failed(result, tool_name))
                data['reason'] = ('Desktop action may still be active; owner must verify and recover'
                                  if data['uncertain'] else 'Agent control')
                self._write(data)
                return result
        except Exception:
            # Hermes skips middleware that raises: NEVER raise on admission failures.
            return json.dumps({'error':'Computer View fence unavailable or busy; desktop operation refused.'})

    def observe(self, capture):
        try:
            with self._lock():
                data = self._read()
                if data['holder']=='human':
                    return json.dumps({'error':'Human has control; observation refused.'})
                if data['in_flight'] or data['uncertain']:
                    return json.dumps({'error':'Desktop operation unsettled; observation refused until owner recovery.'})
                data.update(in_flight=True, reason='A fresh observation is in flight')
                self._write(data)
                token = _OBSERVING.set(str(self.root))
                started_ns = time.time_ns()
                try:
                    result = capture()
                except BaseException:
                    data.update(in_flight=False, uncertain=True,
                                needs_observation=True, reason='Observation did not settle; owner recovery required')
                    self._write(data)
                    raise
                finally:
                    _OBSERVING.reset(token)
                valid = self._fresh_capture(result, started_ns)
                data.update(in_flight=False, uncertain=not valid, needs_observation=not valid)
                self._write(data)
                if not result:
                    return json.dumps({'error':'Observation failed; input remains fenced.'})
                if not valid:
                    return result
                data.update(needs_observation=False, reason='Agent control; new observation received')
                self._event(data, 'Hermes observed the desktop')
                return result
        except Exception:
            return json.dumps({'error':'Computer View observation unavailable; input remains fenced.'})

    @staticmethod
    def _computer_capture_data(value):
        """Recognize computer_use's text capture, which has no success flag."""
        if (value.get('mode') not in ('ax', 'som', 'vision') or
                any(type(value.get(key)) is not int or value[key] <= 0
                    for key in ('width', 'height')) or
                not isinstance(value.get('elements'), list) or
                not all(isinstance(element, dict) for element in value['elements']) or
                type(value.get('total_elements')) is not int or
                value['total_elements'] < len(value['elements']) or
                not all(isinstance(value.get(key), str) for key in ('app', 'window_title')) or
                not isinstance(value.get('summary'), str)):
            return None
        for element in value['elements']:
            if (type(element.get('index')) is not int or element['index'] <= 0 or
                    not all(isinstance(element.get(key), str)
                            for key in ('role', 'label', 'app')) or 'bounds' not in element):
                return None
            bounds = element['bounds']
            if bounds is not None and (not isinstance(bounds, list) or len(bounds) != 4 or
                    not all(type(coordinate) is int for coordinate in bounds) or
                    bounds[2] < 0 or bounds[3] < 0):
                return None
        lines = value['summary'].splitlines()
        if len(lines) < 2:
            return None
        # Auxiliary vision preserves the backend dimensions in JSON but may use
        # downscaled image dimensions in the human summary (Hermes tool.py 963).
        header = re.fullmatch(r'capture mode=(ax|som|vision) ([1-9][0-9]*)x([1-9][0-9]*)(?: (?:app|window)=.*)?', lines[0])
        auxiliary = (value.get('vision_analysis_routed_via') == 'auxiliary.vision' and
                     isinstance(value.get('vision_analysis'), str) and
                     bool(value['vision_analysis'].strip()))
        if (header is None or header[1] != value['mode'] or
                (not auxiliary and (int(header[2]), int(header[3])) !=
                 (value['width'], value['height'])) or
                lines[1] != f"{value['total_elements']} interactable element(s):"):
            return None
        return value

    @classmethod
    def _result_data(cls, result, tool_name='browser_exec'):
        """Inspect supported JSON/native envelopes without modifying model output."""
        try:
            if isinstance(result, str):
                result = json.loads(result)
            if not isinstance(result, dict):
                return None
            def explicit_failure(value):
                return (any(value.get(key) for key in ('error', 'is_error', 'isError')) or
                        value.get('status') == 'error' or
                        ('success' in value and value['success'] is not True) or
                        ('ok' in value and value['ok'] is not True) or
                        ('exit_code' in value and
                         (type(value['exit_code']) is not int or value['exit_code'] != 0)))
            if explicit_failure(result):
                return None
            if (tool_name == 'computer_use' and '_multimodal' in result and
                    result['_multimodal'] is not True):
                return None
            if result.get('_multimodal') is True:
                content = result.get('content')
                if (not isinstance(content, list) or not content or
                        not all(isinstance(part, dict) for part in content) or
                        not any(part.get('type') == 'text' and isinstance(part.get('text'), str)
                                for part in content)):
                    return None
                images = [part.get('image_url', {}).get('url') for part in content
                          if part.get('type') == 'image_url' and isinstance(part.get('image_url'), dict)]
                if not images or not all(isinstance(url, str) and
                        url.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')) and
                        len(url) <= 44 * 1024 * 1024 and
                        len(base64.b64decode(url.split(',', 1)[1], validate=True)) >= 24 for url in images):
                    return None
                if tool_name == 'computer_use':
                    meta, summary = result.get('meta'), result.get('text_summary')
                    if (not isinstance(meta, dict) or explicit_failure(meta) or
                            meta.get('mode') not in ('som', 'vision') or
                            type(meta.get('elements')) is not int or meta['elements'] < 0 or
                            type(meta.get('png_bytes')) is not int or meta['png_bytes'] <= 0 or
                            not isinstance(summary, str) or
                            content[0].get('type') != 'text' or content[0].get('text') != summary):
                        return None
                    if 'action_result' in result:
                        action = result['action_result']
                        if (not isinstance(action, dict) or explicit_failure(action) or
                                action.get('ok') is not True):
                            return None
                        prefix, end = json.JSONDecoder().raw_decode(summary)
                        if prefix != action or summary[end:end + 2] != '\n\n':
                            return None
                        summary = summary[end + 2:]
                    return cls._computer_capture_data({**meta, 'elements': [],
                        'total_elements': meta['elements'], 'summary': summary,
                        'app': '', 'window_title': ''})
                result = json.loads(result['text_summary'])
            if not isinstance(result, dict) or explicit_failure(result):
                return None
            if tool_name == 'computer_use' and 'mode' in result:
                return cls._computer_capture_data(result)
            return result if result.get('success') is True or result.get('ok') is True else None
        except (ValueError, TypeError, KeyError):
            return None

    @classmethod
    def _failed(cls, result, tool_name='browser_exec'):
        return cls._result_data(result, tool_name) is None

    @classmethod
    def _fresh_capture(cls, result, started_ns):
        """The in-process browser's returned path must be a new image on this host.

        Native attachments alone are not evidence of a successful fresh capture:
        Hermes may attach a screenshot even when the subprocess failed.
        """
        summary = cls._result_data(result)
        path = summary.get('screenshot_path') if summary else None
        if not isinstance(path, str) or not path or not os.path.isabs(path):
            return False
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as image:
                meta = os.fstat(image.fileno())
                if (not stat.S_ISREG(meta.st_mode) or meta.st_mtime_ns < started_ns or
                        not 24 <= meta.st_size <= 32 * 1024 * 1024):
                    return False
                head = image.read(24)
                image.seek(-12, os.SEEK_END)
                tail = image.read()
                return ((head.startswith(b'\x89PNG\r\n\x1a\n') and head[12:16] == b'IHDR' and
                         int.from_bytes(head[16:20], 'big') > 0 and
                         int.from_bytes(head[20:24], 'big') > 0 and tail[4:8] == b'IEND') or
                        (head.startswith(b'\xff\xd8\xff') and tail.endswith(b'\xff\xd9')) or
                        (head[:4] == b'RIFF' and head[8:12] == b'WEBP'))
        except (OSError, ValueError):
            return False
