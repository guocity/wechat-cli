"""Report incomplete native installations before preparing or launching an app."""

from importlib.util import find_spec

from .errors import AppError


def require_modules(modules):
    if any(find_spec(name) is None for name in modules):
        raise AppError(
            "platform_dependencies_missing",
            "Required platform dependencies are missing. Run uvx --refresh wechat-cli setup "
            "to reinstall the dependencies selected automatically for this operating system.",
            424,
        )
