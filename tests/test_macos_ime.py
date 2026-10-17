import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from macos_ime import CocoaImeBridge, IME_BINDTAG, MacOSIMEGuard


class MacOSIMEGuardTests(unittest.TestCase):
    def guard(self, widget_class='Entry', *, changed=True):
        guard = object.__new__(MacOSIMEGuard)
        owner = Mock()
        owner.winfo_class.return_value = widget_class
        guard.owner = owner
        guard.root = Mock()
        guard.enabled = True
        guard._finishing = False
        guard._preserve_clears = {}
        guard.bridge = Mock()
        guard.bridge.finish_composition.side_effect = lambda preserve: (preserve() or True) if changed else False
        return guard, owner

    def test_commit_preserves_text_and_suppresses_only_its_queued_clear(self):
        guard, owner = self.guard()
        self.assertTrue(guard.finish())
        self.assertIsNone(guard.owner)
        owner.selection_clear.assert_called_once()
        owner.delete.assert_not_called()
        owner.insert.assert_not_called()
        event = SimpleNamespace(widget=owner)
        self.assertEqual(guard._cleared(event), 'break')
        self.assertIsNone(guard._cleared(event))
        self.assertFalse(guard._preserve_clears)

    def test_text_commit_removes_only_ime_decoration_not_its_contents(self):
        guard, owner = self.guard('Text')
        self.assertTrue(guard.finish())
        owner.tag_remove.assert_called_once_with('IMEmarkedtext', '1.0', 'end')
        owner.delete.assert_not_called()
        owner.insert.assert_not_called()

    def test_ordinary_ime_clear_is_not_blocked(self):
        guard, owner = self.guard()
        self.assertIsNone(guard._cleared(SimpleNamespace(widget=owner)))
        self.assertIsNone(guard.owner)

    def test_no_native_composition_leaves_selection_and_text_alone(self):
        guard, owner = self.guard(changed=False)
        self.assertFalse(guard.finish())
        owner.selection_clear.assert_not_called()
        self.assertFalse(guard._preserve_clears)

    def test_native_input_bindtag_precedes_widget_and_class_bindings(self):
        guard, owner = self.guard()
        owner.bindtags.return_value = ('entry-path', 'Entry', '.', 'all')
        guard.attach(owner)
        owner.bindtags.assert_called_with((IME_BINDTAG, 'entry-path', 'Entry', '.', 'all'))

    def test_click_tab_and_programmatic_focus_loss_finish_before_transfer(self):
        guard, owner = self.guard()
        with patch.object(guard, 'finish') as finish:
            guard._before_pointer(SimpleNamespace(widget=Mock()))
            guard._focus_out(SimpleNamespace(widget=owner))
            guard._focus_out(SimpleNamespace(widget=Mock()))
        self.assertEqual(finish.call_count, 2)

    def test_windows_does_not_install_bindings_or_load_cocoa(self):
        root = Mock()
        guard = MacOSIMEGuard(root, platform_name='win32')
        self.assertFalse(guard.enabled)
        root.bind_class.assert_not_called()
        root.bind_all.assert_not_called()
        root.tk.call.assert_not_called()
        with patch('macos_ime.sys.platform', 'win32'), patch('macos_ime.ctypes.CDLL') as load:
            self.assertFalse(CocoaImeBridge().finish_composition(Mock()))
            load.assert_not_called()

    def test_native_unmark_preserves_queued_clear_before_resetting_input_context(self):
        bridge = CocoaImeBridge()
        events = []
        bridge._marked_views = lambda: [11]

        def send(_obj, selector, *_args, **_kwargs):
            events.append(selector)
            return 22 if selector == 'inputContext' else None

        bridge._send = send
        with patch('macos_ime.sys.platform', 'darwin'):
            self.assertTrue(bridge.finish_composition(lambda: events.append('preserve old text')))
        self.assertEqual(events, ['inputContext', 'preserve old text', 'unmarkText', 'discardMarkedText'])


if __name__ == '__main__':
    unittest.main()
