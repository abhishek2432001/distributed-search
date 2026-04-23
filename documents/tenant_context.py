import threading

_thread_locals = threading.local()
_CONTEXT_KEY = "current_tenant"


def set_current_tenant(tenant) -> None:
    setattr(_thread_locals, _CONTEXT_KEY, tenant)


def get_current_tenant():
    return getattr(_thread_locals, _CONTEXT_KEY, None)


def clear_current_tenant() -> None:
    setattr(_thread_locals, _CONTEXT_KEY, None)
