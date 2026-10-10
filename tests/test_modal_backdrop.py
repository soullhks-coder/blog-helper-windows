import unittest
from types import SimpleNamespace
from unittest.mock import patch

import modal_backdrop
from modal_backdrop import DimmedDialogApi, ModalDimmer, dim_color


class Widget:
    def __init__(self, color="#ffffff", children=()):
        self.colors = {"background": color, "foreground": "#2563eb"}
        self.children = list(children)
        self.callbacks = {}
        self.grabbed = False

    def winfo_rgb(self, color):
        if not isinstance(color, str) or not color.startswith("#"):
            raise ValueError(color)
        return tuple(int(color[i:i + 2], 16) * 257 for i in (1, 3, 5))

    def cget(self, option):
        if option not in self.colors:
            raise ValueError(option)
        return self.colors[option]

    def configure(self, **values):
        self.colors.update(values)

    def winfo_children(self):
        return self.children

    def winfo_ismapped(self):
        return True

    def after(self, _delay, callback):
        self.callbacks["refresh"] = callback
        return "refresh"

    def after_cancel(self, job):
        self.callbacks.pop(job, None)

    def bind(self, event, callback, add=None):
        self.callbacks[event] = callback

    def lift(self):
        pass

    def grab_set(self):
        self.grabbed = True

    def close(self):
        self.callbacks["<Destroy>"](type("Event", (), {"widget": self})())


class ModalBackdropTests(unittest.TestCase):
    def test_light_and_dark_colors_use_the_same_scrim(self):
        widget = Widget()
        self.assertEqual(dim_color(widget, "#ffffff"), "#737373")
        self.assertEqual(dim_color(widget, "#222c3b"), "#0f141b")
        self.assertEqual(dim_color(widget, "transparent"), "transparent")
        self.assertEqual(dim_color(widget, ("#ffffff", "#222c3b")), ("#737373", "#0f141b"))

    def test_background_dims_but_dialog_keeps_original_colors(self):
        background, dialog = Widget(), Widget()
        root = Widget(children=(background, dialog))
        dimmer = ModalDimmer(root)
        dimmer.attach(dialog)
        self.assertEqual(background.cget("background"), "#737373")
        self.assertEqual(dialog.cget("background"), "#ffffff")
        dialog.close()
        self.assertEqual(background.cget("background"), "#ffffff")
        self.assertFalse(dimmer.active)
        self.assertFalse(root.callbacks)

    def test_live_update_is_preserved_after_close(self):
        root = Widget()
        dimmer = ModalDimmer(root)
        dimmer.begin()
        root.configure(foreground="#00ff00")
        dimmer.refresh()
        self.assertEqual(root.cget("foreground"), "#007300")
        dimmer.end()
        self.assertEqual(root.cget("foreground"), "#00ff00")

    def test_refresh_does_not_compound_dimming(self):
        root = Widget()
        dimmer = ModalDimmer(root)
        dimmer.begin()
        for _ in range(5):
            dimmer.refresh()
            self.assertEqual(root.cget("background"), "#737373")
        dimmer.end()
        self.assertEqual(root.cget("background"), "#ffffff")

    def test_nested_popups_restore_only_after_last_popup(self):
        first, second = Widget(), Widget()
        root = Widget(children=(first, second))
        dimmer = ModalDimmer(root)
        dimmer.attach(first)
        dimmer.attach(second)
        second.close()
        self.assertTrue(dimmer.active)
        self.assertTrue(first.grabbed)
        self.assertEqual(root.cget("background"), "#737373")
        first.close()
        self.assertFalse(dimmer.active)
        self.assertEqual(root.cget("background"), "#ffffff")

    def test_modal_error_also_cleans_up(self):
        root = Widget()
        root.update_idletasks = lambda: None
        dimmer = ModalDimmer(root)
        with self.assertRaises(RuntimeError):
            with dimmer.temporarily():
                self.assertTrue(dimmer.active)
                raise RuntimeError("native popup failure")
        self.assertFalse(dimmer.active)
        self.assertEqual(root.cget("background"), "#ffffff")

    def test_native_dialog_api_preserves_return_value_and_restores_background(self):
        root = Widget()
        root.winfo_toplevel = lambda: root
        root.update_idletasks = lambda: None

        def ask(*args, **kwargs):
            self.assertTrue(modal_backdrop.modal_dimmer(root).active)
            self.assertEqual(root.cget("background"), "#737373")
            return "chosen.png"

        api = DimmedDialogApi(SimpleNamespace(askopenfilename=ask))
        with patch.object(modal_backdrop.tk, "_default_root", root):
            self.assertEqual(api.askopenfilename(title="파일 선택"), "chosen.png")
        self.assertFalse(modal_backdrop.modal_dimmer(root).active)
        self.assertEqual(root.cget("background"), "#ffffff")
