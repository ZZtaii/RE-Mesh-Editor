"""Shared mesh operation feedback, including callers that bypass UI operators.

Progress is phase-based, not a time estimate. It does not turn Blender's
synchronous import/export into a cancellable background task.
"""
from functools import wraps
import os
import sys
import time
import traceback

_stack = []


def _bpy():
    # Source-table utilities also run outside Blender.
    try:
        import bpy
        return bpy
    except ImportError:
        return None


class WindowsConsole:
    """Only hide a console this operation opened, and only on success."""
    def __init__(self):
        self.handle = None
        self.opened = False
        self.user32 = None

    def open(self):
        bpy = _bpy()
        if bpy is None or bpy.app.background or sys.platform != 'win32':
            return
        try:
            preferences = bpy.context.preferences.addons[__package__.split('.')[0]].preferences
            if not preferences.showConsole:
                return
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            kernel32.GetConsoleWindow.argtypes = []
            kernel32.GetConsoleWindow.restype = wintypes.HWND
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = wintypes.BOOL
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow.restype = wintypes.BOOL
            handle = kernel32.GetConsoleWindow()
            was_visible = bool(handle and user32.IsWindowVisible(handle))
            if not handle:
                # No console exists yet. Only this known state permits a toggle.
                bpy.ops.wm.console_toggle()
                handle = kernel32.GetConsoleWindow()
            elif not was_visible:
                user32.ShowWindow(handle, 4)  # SW_SHOWNOACTIVATE
            self.handle, self.user32 = handle, user32
            self.opened = bool(handle and not was_visible and user32.IsWindowVisible(handle))
        except Exception:
            # UI feedback must never block an otherwise valid mesh operation.
            # Do not blindly toggle on an unknown console state.
            self.opened = False

    def close(self, success):
        if success and self.opened and self.handle and self.user32:
            try:
                self.user32.ShowWindow(self.handle, 0)  # SW_HIDE
            except Exception:
                pass
        self.opened = False


class OperationProgress:
    def __init__(self, label):
        self.label = label
        self.console = WindowsConsole()
        self.started = time.perf_counter()
        self.root = not _stack
        self.wm = None
        self.last_value = 0

    def begin(self):
        if self.root:
            self.console.open()
            bpy = _bpy()
            if bpy is not None and not bpy.app.background:
                try:
                    self.wm = bpy.context.window_manager
                    self.wm.progress_begin(0, 100)
                except Exception:
                    self.wm = None
        _stack.append(self)
        self.stage('Started', 0)

    def stage(self, message, value=None):
        elapsed = time.perf_counter() - self.started
        try:
            print(f'[RE Mesh | {self.label} | {elapsed:.1f}s] {message}', flush=True)
        except (OSError, ValueError):
            pass
        if self.wm is not None and value is not None:
            self.last_value = max(self.last_value, min(100, max(0, value)))
            try:
                self.wm.progress_update(self.last_value)
            except Exception:
                pass

    def finish(self, success):
        self.stage('Finished' if success else 'FAILED — see the error above', 100 if success else None)
        if self.wm is not None:
            try:
                self.wm.progress_end()
            except Exception:
                pass
        if _stack and _stack[-1] is self:
            _stack.pop()
        elif self in _stack:
            _stack.remove(self)
        if self.root:
            self.console.close(success)


def stage(message, value=None):
    if _stack:
        _stack[-1].stage(message, value)


def tracked_operation(kind):
    def decorate(function):
        @wraps(function)
        def wrapped(filePath, options):
            sf6 = str(filePath).endswith('.mesh.230110883')
            preserving = options.get('importBlendShapes' if kind == 'Import' else 'exportBlendShapes', True)
            mode = ('Hybrid' if kind == 'Export' and options.get('sf6HybridPreserve') else
                    'Source preserve' if sf6 and preserving else 'Ordinary')
            progress = OperationProgress(f'{kind} / {mode} / {os.path.basename(filePath)}')
            success = False
            progress.begin()
            try:
                result = function(filePath, options)
                success = result is not False
                if kind == 'Import' and isinstance(result, tuple) and len(result) == 2:
                    success = not result[1]
                return result
            except Exception:
                traceback.print_exc()
                raise
            finally:
                progress.finish(success)
        return wrapped
    return decorate
