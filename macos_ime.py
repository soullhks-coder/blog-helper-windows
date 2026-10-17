"""Finish Aqua's pending text composition before changing Tk input fields.

Tk's macOS NSTextInputClient keeps composing text on the shared content view,
not on each Entry/Text. A late commit after focus changes can erase the old
field's marked text and insert it into the new field. See Tk's implementation:
https://github.com/tcltk/tk/blob/core-8-6-branch/macosx/tkMacOSXKeyEvent.c

Unmarking Tk's content view normally queues TkClearIMEMarkedText, which deletes
the displayed composition. Suppress only that specific clear while finishing:
the visible text stays in its original field, and Cocoa discards the pending
IME buffer. Ordinary IME replacement/deletion events must still reach Tk.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys
import threading
import tkinter as tk


IME_BINDTAG = 'BlogHelperMacImeBoundary'


class CocoaImeBridge:
    """Small, lazy Objective-C bridge; never casts a Tk window ID to NSView."""

    def __init__(self):
        self._objc = None
        self._messages = {}

    def _load(self):
        if self._objc is None:
            self._objc = ctypes.CDLL(ctypes.util.find_library('objc'))
            self._objc.objc_getClass.argtypes = [ctypes.c_char_p]
            self._objc.objc_getClass.restype = ctypes.c_void_p
            self._objc.sel_registerName.argtypes = [ctypes.c_char_p]
            self._objc.sel_registerName.restype = ctypes.c_void_p
        return self._objc

    def _send(self, obj, selector, result=ctypes.c_void_p, args=(), types=()):
        lib = self._load()
        signature = (result, types)
        if signature not in self._messages:
            self._messages[signature] = ctypes.CFUNCTYPE(
                result, ctypes.c_void_p, ctypes.c_void_p, *types)(('objc_msgSend', lib))
        return self._messages[signature](obj, lib.sel_registerName(selector.encode()), *args)

    def _marked_views(self):
        lib = self._load()
        app_class = lib.objc_getClass(b'NSApplication')
        view_class = lib.objc_getClass(b'TKContentView')
        if not app_class or not view_class:
            return []
        app = self._send(app_class, 'sharedApplication')
        windows = self._send(app, 'windows')
        views = []
        for index in range(self._send(windows, 'count', ctypes.c_size_t)):
            window = self._send(windows, 'objectAtIndex:', args=(index,), types=(ctypes.c_size_t,))
            view = self._send(window, 'contentView')
            if (view and self._send(view, 'isKindOfClass:', ctypes.c_bool,
                                   (view_class,), (ctypes.c_void_p,))
                    and self._send(view, 'hasMarkedText', ctypes.c_bool)):
                views.append(view)
        return views

    def finish_composition(self, preserve_clear):
        if sys.platform != 'darwin' or threading.current_thread() is not threading.main_thread():
            return False
        views = self._marked_views()
        for view in views:
            # Keep the original field's displayed text when Tk queues its
            # normal clear event. Do not insert, replace, or copy any text.
            context = self._send(view, 'inputContext')
            preserve_clear()
            self._send(view, 'unmarkText', None)
            if context:
                self._send(context, 'discardMarkedText', None)
        return bool(views)


class MacOSIMEGuard:
    def __init__(self, root, *, bridge=None, platform_name=None):
        self.root = root
        self.bridge = bridge if bridge is not None else CocoaImeBridge()
        self.owner = None
        self._preserve_clears = {}
        self._finishing = False
        self.enabled = (sys.platform if platform_name is None else platform_name) == 'darwin'
        if not self.enabled or root.tk.call('tk', 'windowingsystem') != 'aqua':
            self.enabled = False
            return
        for sequence, callback in (
            ('<<TkStartIMEMarkedText>>', self._started),
            ('<<TkEndIMEMarkedText>>', self._started),
            ('<<TkClearIMEMarkedText>>', self._cleared),
            ('<ButtonPress-1>', self._before_pointer),
            ('<KeyPress-Tab>', self._before_pointer),
            ('<KeyPress-ISO_Left_Tab>', self._before_pointer),
            ('<FocusOut>', self._focus_out),
            ('<Destroy>', self._destroyed),
        ):
            root.bind_class(IME_BINDTAG, sequence, callback)
        root.bind_all('<FocusIn>', self._focused, add='+')
        pending = [root]
        while pending:
            widget = pending.pop()
            self.attach(widget)
            pending.extend(widget.winfo_children())

    def attach(self, widget):
        if not self.enabled or widget is None:
            return
        try:
            if widget.winfo_class() not in {'Entry', 'Text', 'TEntry'}:
                return
            tags = tuple(widget.bindtags())
            if IME_BINDTAG not in tags:
                # Must run before Entry/Text's click binding changes focus.
                widget.bindtags((IME_BINDTAG, *tags))
        except (AttributeError, tk.TclError):
            pass

    def _focused(self, event):
        self.attach(event.widget)

    def _started(self, event):
        self.owner = event.widget

    def _cleared(self, event):
        key = str(event.widget)
        remaining = self._preserve_clears.get(key, 0)
        if remaining:
            if remaining == 1:
                self._preserve_clears.pop(key, None)
            else:
                self._preserve_clears[key] = remaining - 1
            return 'break'
        if self.owner is event.widget:
            self.owner = None
        return None

    def _before_pointer(self, _event):
        self.finish()

    def _focus_out(self, event):
        if self.owner is event.widget:
            self.finish()

    def _destroyed(self, event):
        self._preserve_clears.pop(str(event.widget), None)
        if self.owner is event.widget:
            self.owner = None

    def finish(self):
        if not self.enabled or self.owner is None or self._finishing:
            return False
        owner = self.owner
        key = str(owner)

        def preserve_clear():
            self._preserve_clears[key] = self._preserve_clears.get(key, 0) + 1

        self._finishing = True
        try:
            if not self.bridge.finish_composition(preserve_clear):
                return False
            if owner.winfo_class() == 'Text':
                owner.tag_remove('IMEmarkedtext', '1.0', 'end')
            else:
                owner.selection_clear()
            self.owner = None
            return True
        except (AttributeError, OSError, TypeError, tk.TclError):
            # Unsupported runtimes retain their normal IME handling; never
            # guess native pointers or clear an input field as a fallback.
            return False
        finally:
            self._finishing = False
