"""Hermes native registration; no network, credential or backend I/O on load."""
import json
from pathlib import Path

TOOL_DESCRIPTIONS = {
    'open': 'Send the owner a private Computer View button in Telegram. Never returns the link.',
    'status': 'Read private Computer View control status.',
    'revoke': 'Revoke all Computer View links and sessions for this owner.',
    'handoff': 'Request user takeover of Computer View.',
    'observe': 'Capture a fresh screenshot after user handback before continuing computer actions.',
}


def register(ctx):
    def handler_for(action):
        def handler(args, **kwargs):
            try:
                from .computer_view.plugin_runtime import Runtime
                return Runtime(ctx).call(action, args, **kwargs)
            except Exception:
                return json.dumps({'ok': False, 'error': 'computer_view_unavailable'})
        return handler

    for action, description in TOOL_DESCRIPTIONS.items():
        name = 'computer_view_' + action
        ctx.register_tool(name=name, toolset='computer-view', schema={
            'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        }, handler=handler_for(action))
    ctx.register_skill('computer-view', Path(__file__).parent / 'skills' / 'computer-view' / 'SKILL.md',
                       description='Private Computer View and user handoff.')
    def guard(tool_name, args, next_call, **kwargs):
        if tool_name not in {'browser_exec', 'computer_use', 'browser_vault'}:
            return next_call(args)
        try:
            from .computer_view.plugin_runtime import Runtime
            return Runtime(ctx).execute(tool_name, args, next_call, **kwargs)
        except Exception:
            # Hermes skips raised middleware failures. Return denial, never exception text.
            return json.dumps({'ok': False, 'error': 'computer_view_guard_unavailable'})
    ctx.register_middleware('tool_execution', guard)

    def setup_cli(parser):
        import argparse
        parser.add_argument('computer_view_args', nargs=argparse.REMAINDER, metavar='ARGS',
                            help='setup/status/serve/doctor/assets/link/revoke/recover; use -- before leading options')

    def run_cli(args):
        from .computer_view.cli import main
        argv = list(args.computer_view_args)
        if argv[:1] == ['--']:
            argv = argv[1:]
        return main(argv)

    ctx.register_cli_command('computer-view', 'Private Computer View setup and service',
                             setup_fn=setup_cli, handler_fn=run_cli,
                             description='Run hermes computer-view status or setup. For complete CLI help: hermes computer-view -- --help')
