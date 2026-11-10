"""Native Hermes directory-plugin entrypoint; registration is inert."""


def register(ctx):
    from .plugin import register as register_plugin
    return register_plugin(ctx)


__all__ = ['register']
