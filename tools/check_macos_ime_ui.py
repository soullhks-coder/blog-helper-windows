"""Real Aqua marked-text regression, without changing the OS keyboard layout."""
from pathlib import Path
import ctypes
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import customtkinter as ctk
from input_surfaces import InputComboBox, InputEntry, InputTextbox
from macos_ime import CocoaImeBridge, MacOSIMEGuard


class NSRange(ctypes.Structure):
    _fields_ = [('location', ctypes.c_size_t), ('length', ctypes.c_size_t)]


def check(theme):
    ctk.set_appearance_mode(theme)
    ctk.set_widget_scaling(.82)
    root = ctk.CTk()
    root.title('Blog Helper macOS input test (isolated)')
    root.geometry('650x500')
    first = InputEntry(root, placeholder_text='first input')
    first.pack(pady=12)
    variable = ctk.StringVar(value='')
    second = InputEntry(root, placeholder_text='second input', textvariable=variable)
    second.pack(pady=12)
    box = InputTextbox(root, height=100)
    box.pack(pady=12)
    combo = InputComboBox(root, values=['first', 'second'])
    combo.pack(pady=12)
    errors = []
    root.report_callback_exception = lambda *error: errors.append(error)
    root.update()
    first.focus_force()
    root.update()
    bridge = CocoaImeBridge()
    lib = bridge._load()
    app = bridge._send(lib.objc_getClass(b'NSApplication'), 'sharedApplication')
    window = bridge._send(app, 'keyWindow')
    view = bridge._send(window, 'contentView')
    if not view:
        # Hosted GUI runners may have logical Tk focus without a key NSWindow.
        # Resolve the test's real Tk view from Cocoa rather than casting IDs.
        windows = bridge._send(app, 'windows')
        tk_view_class = lib.objc_getClass(b'TKContentView')
        for index in range(bridge._send(windows, 'count', ctypes.c_size_t)):
            candidate = bridge._send(windows, 'objectAtIndex:', args=(index,), types=(ctypes.c_size_t,))
            candidate = bridge._send(candidate, 'contentView')
            if bridge._send(candidate, 'isKindOfClass:', ctypes.c_bool, (tk_view_class,), (ctypes.c_void_p,)):
                view = candidate
                break
    assert view

    def native_text(selector, text, *, marked=False):
        string = bridge._send(lib.objc_getClass(b'NSString'), 'stringWithUTF8String:',
                              args=(text.encode('utf-8'),), types=(ctypes.c_char_p,))
        replacement = NSRange(ctypes.c_size_t(-1).value, 0)
        if marked:
            bridge._send(view, selector, None, (string, NSRange(len(text), 0), replacement),
                         (ctypes.c_void_p, NSRange, NSRange))
        else:
            bridge._send(view, selector, None, (string, replacement), (ctypes.c_void_p, NSRange))
        root.update()
        assert not errors, errors

    def compose(text):
        native_text('setMarkedText:selectedRange:replacementRange:', text, marked=True)
        assert bridge._send(view, 'hasMarkedText', ctypes.c_bool)

    def focus(widget):
        widget.focus_force()
        root.update()

    def reset():
        first.delete(0, 'end')
        second.delete(0, 'end')
        box.delete('1.0', 'end')
        combo.set('')
        root.update()

    try:
        # Demonstrate the underlying Tk behavior before installing the guard:
        # a pending native commit moves the old text into the newly focused field.
        focus(first)
        compose('마지막')
        focus(second)
        native_text('insertText:replacementRange:', '마지막')
        assert first.get() == '' and second.get() == '마지막'
        reset()
        guard = MacOSIMEGuard(root, bridge=bridge)
        assert guard.enabled

        for source, destination in ((first._entry, second._entry),
                                    (first._entry, box._textbox),
                                    (box._textbox, first._entry),
                                    (combo._entry, second._entry)):
            for movement in ('click', 'programmatic', 'tab'):
                reset()
                focus(source)
                compose('한글마지막')
                assert guard.owner is source
                if movement == 'click':
                    destination.event_generate('<ButtonPress-1>', x=8, y=8)
                    destination.event_generate('<ButtonRelease-1>', x=8, y=8)
                    root.update()
                elif movement == 'tab':
                    # Exercise the pre-class traversal boundary, then direct
                    # focus to the test target to cover both native field kinds.
                    source.event_generate('<KeyPress-Tab>')
                    focus(destination)
                else:
                    focus(destination)
                root.update()
                assert not bridge._send(view, 'hasMarkedText', ctypes.c_bool), movement
                read = lambda widget: widget.get('1.0', 'end-1c') if widget.winfo_class() == 'Text' else widget.get()
                expected_source = '한글마지막' + ('\t' if movement == 'tab' and source.winfo_class() == 'Text' else '')
                assert read(source) == expected_source, (theme, movement, source, read(source))
                assert read(destination) == '', (theme, movement, destination, read(destination))
                # The next composition must start fresh in the destination.
                compose('새문구')
                guard.finish()
                root.update()
                assert read(source) == expected_source
                assert read(destination) == '새문구'
                assert not guard._preserve_clears and not errors, errors

        # Normal replacement/backspace during composition must not be blocked.
        reset()
        focus(first)
        for text in ('ㅎ', '하', '한', '한ㄱ', '한그', '한글'):
            compose(text)
            assert first.get() == text
        native_text('insertText:replacementRange:', '한글')
        assert first.get() == '한글'
        assert not bridge._send(view, 'hasMarkedText', ctypes.c_bool)
        focus(second)
        assert second.get() == variable.get() == ''
        # Dialog editors created after installation are tagged on FocusIn too.
        dynamic = InputEntry(root, placeholder_text='new dialog field')
        dynamic.pack(pady=8)
        root.update()
        focus(dynamic)
        compose('새편집창')
        focus(first)
        assert dynamic.get() == '새편집창' and first.get() == '한글'
        assert not bridge._send(view, 'hasMarkedText', ctypes.c_bool)
        dynamic.destroy()
        print(f'{sys.platform} {theme}: native late-commit reproduced; 12 guarded Entry/Text/Combo focus transfers, fresh typing and normal composition passed', flush=True)
    finally:
        root.destroy()


if __name__ == '__main__':
    if sys.platform != 'darwin':
        print('macOS native IME test skipped on this OS', flush=True)
    else:
        for theme in ('light', 'dark'):
            check(theme)
