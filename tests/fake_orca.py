"""Stand-in for the `orca` module the OrcaSlicer plugin host injects."""

import enum
import json
import sys
import threading
import types


class LifecycleEvent(enum.Enum):
    PresetSelected = 1
    PresetSaved = 2
    ProjectOpened = 3


class NotificationLevel(enum.Enum):
    RegularNotificationLevel = 1
    WarningNotificationLevel = 2


class ExecutionResult:
    def __init__(self, ok, message):
        self.ok, self.message = ok, message

    @staticmethod
    def success(message=""):
        return ExecutionResult(True, message)


class Window:
    def __init__(self, **kw):
        self.kw, self.posts, self.open = kw, [], True

    def post(self, data):
        self.posts.append(data)

    def is_open(self):
        return self.open


class Capability:
    config = "{}"

    def get_config(self):
        return self.config

    def save_config(self, text):
        self.config = text
        return True


def install(storage, bundle=None):
    """Put a fresh fake `orca` into sys.modules and return it."""
    m = types.ModuleType("orca")
    m.permissions, m.capabilities, m.notifications, m.windows = [], [], [], []
    m.LifecycleEvent = LifecycleEvent
    m.ExecutionResult = ExecutionResult
    m.base = type("base", (), {})
    m.plugin = lambda cls: cls
    m.request_permissions = lambda **kw: m.permissions.append(kw)
    m.register_capability = m.capabilities.append
    m.script = types.SimpleNamespace(ScriptPluginCapabilityBase=Capability)

    def preset_bundle():
        if bundle is None:
            raise RuntimeError("Preset bundle is not available")
        return bundle

    def storage_in_callback():
        # Orca 2.5.0 only answers storage() inside a plugin callback, never from a worker thread.
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("plugin.storage() must be called from a plugin callback")
        return str(storage)

    def push_notification(level, text):
        m.notifications.append((level, text))

    def create_window(**kw):
        w = Window(**kw)
        m.windows.append(w)
        return w

    m.host = types.SimpleNamespace(
        preset_bundle=preset_bundle,
        plugin=types.SimpleNamespace(storage=storage_in_callback),
        ui=types.SimpleNamespace(
            NotificationLevel=NotificationLevel,
            push_notification=push_notification,
            create_window=create_window,
        ),
    )
    sys.modules["orca"] = m
    sys.modules.pop("orca_git_sync", None)
    return m


def config(**values):
    return json.dumps(values)
