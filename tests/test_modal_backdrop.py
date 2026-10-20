import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import modal_backdrop
from modal_backdrop import DimmedDialogApi, ModalDimmer, dim_color


class Widget:
    def __init__(self, color="#ffffff", children=()):
        self.colors = {"background": color, "foreground": "#2563eb"}
        self.children = list(children)
        self.callbacks = {}
        self.grabbed = False
        self.writes = []

    def winfo_rgb(self, color):
        if not isinstance(color, str) or not color.startswith("#"):
            raise ValueError(color)
        return tuple(int(color[i:i + 2], 16) * 257 for i in (1, 3, 5))

    def cget(self, option):
        if option not in self.colors:
            raise ValueError(option)
        return self.colors[option]

    def configure(self, **values):
        self.writes.append(values.copy())
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
    def test_basic_container_paint_does_not_propagate_into_hidden_children(self):
        class Frame:
            def __init__(self):
                self._fg_color = '#ffffff'
                self._bg_color = '#ffffff'
                self._border_color = '#abcdef'
                self._draw = Mock()
                self.configure = Mock(side_effect=AssertionError('recursive public container setter'))

        widget = Frame()
        with patch.object(modal_backdrop.ctk, 'CTkFrame', Frame):
            ModalDimmer._configure_colors(widget, dict(fg_color='#737373', bg_color='#737373', border_color='#4d5c6c'))
        self.assertEqual(widget._fg_color, '#737373')
        self.assertEqual(widget._bg_color, '#737373')
        self.assertEqual(widget._border_color, '#4d5c6c')
        widget._draw.assert_called_once_with()

    def test_composite_colors_and_image_use_one_redraw_and_skip_unchanged_values(self):
        class Composite(Widget):
            pass

        widget = Composite()
        widget.colors.update(fg_color='#ffffff', bg_color='#ffffff', border_color='#abcdef', image='old')
        targets = dict(fg_color='#737373', bg_color='#737373', border_color='#4d5c6c', image='new')
        records = [((widget, option), lambda o=option: widget.cget(o),
                    lambda value, o=option: widget.configure(**{o: value}), target)
                   for option, target in targets.items()]
        with patch.object(modal_backdrop.ctk, 'CTkBaseClass', Composite):
            ModalDimmer._apply_values(records)
            self.assertEqual(widget.writes, [targets])
            ModalDimmer._apply_values(records)
            self.assertEqual(widget.writes, [targets])

    def test_batched_restore_compares_children_after_parent_propagation(self):
        class Composite(Widget):
            def configure(self, **values):
                super().configure(**values)
                for child in self.children:
                    child.colors['background'] = '#737373'

        child = Composite()
        parent = Composite(children=(child,))
        parent.colors['background'] = '#737373'
        records = [((widget, 'background'), lambda w=widget: w.cget('background'),
                    lambda value, w=widget: w.configure(background=value), '#ffffff')
                   for widget in (parent, child)]
        with patch.object(modal_backdrop.ctk, 'CTkBaseClass', Composite):
            ModalDimmer._apply_values(records)
        self.assertEqual(parent.cget('background'), '#ffffff')
        self.assertEqual(child.cget('background'), '#ffffff')

    def test_native_scroll_content_and_canvas_items_keep_their_own_setters(self):
        widget = Widget()
        calls = []
        records = [((widget, 1, option), lambda: 'old',
                    lambda value, o=option: calls.append((o, value)), 'new')
                   for option in ('fill', 'outline')]
        records.append(((widget, 'background'), lambda: 'old',
                        lambda value: calls.append(('native frame', value)), 'new'))
        ModalDimmer._apply_values(records)
        self.assertEqual(calls, [('fill', 'new'), ('outline', 'new'), ('native frame', 'new')])
        self.assertFalse(widget.writes)

    def test_empty_snapshot_does_not_walk_all_hidden_pages(self):
        root = Widget()
        with patch.object(root, 'winfo_children', side_effect=AssertionError('unnecessary full-tree scan')):
            self.assertEqual(ModalDimmer(root)._restoration_values(), {})

    def test_disappearing_widget_cannot_abort_refresh_and_leave_dim_stale(self):
        class DestroyedWidget(Widget):
            def winfo_ismapped(self):
                raise modal_backdrop.tk.TclError('widget destroyed during UI refresh')

        for platform in ('darwin', 'win32'):
            root = Widget()
            dimmer = ModalDimmer(root, platform_name=platform)
            dimmer.begin()
            root.children.append(DestroyedWidget())
            dimmer.refresh()
            self.assertTrue(dimmer.active)
            dimmer.end()
            self.assertFalse(dimmer.active)
            self.assertFalse(dimmer._windows_saved)
            self.assertEqual(root.cget('background'), '#ffffff')

    def test_old_idle_close_cannot_release_new_dialog(self):
        old, new = Widget(), Widget()
        root = Widget(children=(old, new))
        idle = []
        root.after_idle = idle.append
        dimmer = ModalDimmer(root)
        dimmer.attach(old)
        old.close()
        dimmer.detach(old)
        dimmer.attach(new)
        idle.pop(0)()
        self.assertEqual(dimmer._depth, 1)
        self.assertEqual(root.cget('background'), '#737373')
        new.close()
        dimmer.detach(new)
        self.assertFalse(dimmer.active)
        self.assertEqual(root.cget('background'), '#ffffff')

    def test_destroyed_descendant_does_not_abort_remaining_restoration(self):
        class DestroyedWidget(Widget):
            def cget(self, option):
                raise modal_backdrop.tk.TclError('widget destroyed')

            def winfo_children(self):
                raise modal_backdrop.tk.TclError('widget destroyed')

        root = Widget()
        dimmer = ModalDimmer(root)
        dimmer.begin()
        root.children.append(DestroyedWidget())
        dimmer.end()
        self.assertFalse(dimmer.active)
        self.assertFalse(dimmer._windows_saved)
        self.assertEqual(root.cget('background'), '#ffffff')

    def test_explicit_close_restores_before_navigation_and_idle_callback_is_idempotent(self):
        dialog = Widget()
        root = Widget(children=(dialog,))
        idle = []
        root.after_idle = idle.append
        dimmer = ModalDimmer(root)
        dimmer.attach(dialog)
        dialog.close()
        self.assertTrue(dimmer.active)
        dimmer.detach(dialog)
        self.assertFalse(dimmer.active)
        self.assertFalse(dimmer._windows_saved)
        self.assertFalse(dimmer._pending_closes)
        self.assertEqual(root.cget('background'), '#ffffff')
        idle.pop()()
        dimmer.detach(dialog)
        self.assertEqual(dimmer._depth, 0)

    def test_explicit_close_only_releases_its_own_nested_modal(self):
        first, guide = Widget(), Widget()
        root = Widget(children=(first, guide))
        idle = []
        root.after_idle = idle.append
        dimmer = ModalDimmer(root)
        dimmer.attach(first)
        dimmer.attach(guide)
        guide.close()
        dimmer.detach(guide)
        self.assertEqual(dimmer._depth, 1)
        self.assertEqual(root.cget('background'), '#737373')
        self.assertTrue(first.grabbed)
        idle.pop()()
        self.assertEqual(dimmer._depth, 1)
        first.close()
        dimmer.detach(first)
        self.assertFalse(dimmer.active)
        self.assertEqual(root.cget('background'), '#ffffff')

    def test_repeated_attach_does_not_leak_modal_depth(self):
        dialog = Widget()
        root = Widget(children=(dialog,))
        dimmer = ModalDimmer(root)
        dimmer.attach(dialog)
        dimmer.attach(dialog)
        self.assertEqual(dimmer._depth, 1)
        dialog.close()
        self.assertFalse(dimmer.active)

    def test_dialog_restoration_waits_until_native_destroy_event_finishes(self):
        dialog = Widget()
        root = Widget(children=(dialog,))
        idle = []
        root.after_idle = idle.append
        dimmer = ModalDimmer(root)
        dimmer.attach(dialog)
        dialog.close()
        self.assertTrue(dimmer.active)
        self.assertEqual(root.cget('background'), '#737373')
        self.assertEqual(len(idle), 1)
        idle.pop()()
        self.assertFalse(dimmer.active)
        self.assertEqual(root.cget('background'), '#ffffff')

    def test_already_normal_hidden_child_survives_old_parent_background_propagation(self):
        class TransparentParent(Widget):
            def configure(self, **values):
                if 'background' in values:
                    for child in self.children:
                        child.configure(background=self.colors['background'])
                super().configure(**values)

        for platform in ('darwin', 'win32'):
            child = Widget('#f6f8fc')
            child.winfo_ismapped = lambda: False
            root = TransparentParent('#f6f8fc', children=(child,))
            dimmer = ModalDimmer(root, platform_name=platform)
            dimmer.begin()
            self.assertEqual(child.cget('background'), '#f6f8fc')
            dimmer.end()
            self.assertEqual(child.cget('background'), '#f6f8fc', platform)

    def test_new_hidden_inherited_background_is_restored_before_first_refresh(self):
        for platform in ('darwin', 'win32'):
            root = Widget('#f6f8fc')
            dimmer = ModalDimmer(root, platform_name=platform)
            dimmer.begin()
            child = Widget(root.cget('background'))
            child.winfo_ismapped = lambda: False
            root.children.append(child)
            dimmer.end()
            self.assertEqual(child.cget('background'), '#f6f8fc', platform)

    def test_new_inherited_background_is_not_saved_as_dimmed_original(self):
        for platform in ('darwin', 'win32'):
            root = Widget('#f6f8fc')
            dimmer = ModalDimmer(root, platform_name=platform)
            dimmer.begin()
            child = Widget(root.cget('background'))
            explicit = Widget('#abcdef')
            root.children.extend((child, explicit))
            for _ in range(3):
                dimmer.refresh()
                self.assertEqual(child.cget('background'), '#6f7071', platform)
            dimmer.end()
            self.assertEqual(child.cget('background'), '#f6f8fc', platform)
            self.assertEqual(explicit.cget('background'), '#abcdef', platform)

    def test_restore_snapshots_child_live_changes_before_parent_propagation(self):
        class Parent(Widget):
            def configure(self, **values):
                super().configure(**values)
                if 'background' in values:
                    for child in self.children:
                        child.configure(background=values['background'])

        for platform in ('darwin', 'win32'):
            child = Widget('#ffffff')
            root = Parent(children=(child,))
            dimmer = ModalDimmer(root, platform_name=platform)
            dimmer.begin()
            child.configure(background='#abcdef')
            dimmer.end()
            self.assertEqual(child.cget('background'), '#abcdef', platform)

    def test_windows_refresh_never_brightens_or_repaints_unchanged_widgets(self):
        child, dialog = Widget(), Widget()
        root = Widget(children=(child, dialog))
        dimmer = ModalDimmer(root, platform_name="win32")
        dimmer.attach(dialog)
        root.writes.clear()
        child.writes.clear()
        for _ in range(10):
            dimmer.refresh()
            self.assertEqual(root.cget("background"), "#737373")
            self.assertEqual(child.cget("background"), "#737373")
            self.assertEqual(dialog.cget("background"), "#ffffff")
        self.assertEqual(root.writes, [])
        self.assertEqual(child.writes, [])
        dialog.close()
        self.assertEqual(child.cget("background"), "#ffffff")
        self.assertEqual(root.cget("background"), "#ffffff")
        self.assertFalse(dimmer._windows_saved)

    def test_windows_live_update_dims_only_new_value_and_restores_latest(self):
        root = Widget()
        dimmer = ModalDimmer(root, platform_name="win32")
        dimmer.begin()
        root.configure(foreground="#00ff00")
        root.writes.clear()
        dimmer.refresh()
        self.assertEqual(root.writes, [{"foreground": "#007300"}])
        dimmer.refresh()
        self.assertEqual(len(root.writes), 1)
        dimmer.end()
        self.assertEqual(root.cget("foreground"), "#00ff00")

    def test_windows_external_update_without_refresh_is_not_overwritten(self):
        root = Widget()
        dimmer = ModalDimmer(root, platform_name="win32")
        dimmer.begin()
        root.configure(background="#00ff00")
        dimmer.end()
        self.assertEqual(root.cget("background"), "#00ff00")

    def test_windows_new_widgets_and_nested_dialogs_keep_one_scrim(self):
        first = Widget()
        root = Widget(children=(first,))
        dimmer = ModalDimmer(root, platform_name="win32")
        dimmer.attach(first)
        new_child, second = Widget("#222c3b"), Widget()
        root.children.extend((new_child, second))
        dimmer.attach(second)
        dimmer.refresh()
        self.assertEqual(new_child.cget("background"), "#0f141b")
        self.assertEqual(second.cget("background"), "#ffffff")
        second.close()
        self.assertTrue(dimmer.active)
        self.assertTrue(first.grabbed)
        self.assertEqual(root.cget("background"), "#737373")
        first.close()
        self.assertEqual(new_child.cget("background"), "#222c3b")
        self.assertFalse(root.callbacks)

    def test_windows_manual_refresh_keeps_single_timer(self):
        class TimerWidget(Widget):
            def after(self, delay, callback):
                key = f"job{len(self.callbacks)}"
                while key in self.callbacks:
                    key += "x"
                self.callbacks[key] = callback
                return key

        for platform in ('win32', 'darwin'):
            root = TimerWidget()
            dimmer = ModalDimmer(root, platform_name=platform)
            dimmer.begin()
            for _ in range(5):
                dimmer.refresh()
                self.assertEqual(len(root.callbacks), 1, platform)
            dimmer.end()
            self.assertFalse(root.callbacks)

    def test_macos_refresh_keeps_existing_restore_behavior(self):
        root = Widget()
        dimmer = ModalDimmer(root, platform_name="darwin")
        dimmer.begin()
        root.writes.clear()
        dimmer.refresh()
        self.assertIn({"background": "#ffffff"}, root.writes)
        self.assertEqual(root.cget("background"), "#737373")
        dimmer.end()

    def test_scroll_content_uses_native_background_not_delegated_outer_api(self):
        class ScrollContent(Widget):
            def __init__(self, outer, children):
                super().__init__(children=children)
                self.outer = outer
                self.native_colors = {"background": "#f6f8fc"}

            def cget(self, option):
                return self.outer.cget(option)

            def configure(self, **values):
                self.outer.configure(**values)

        row = Widget("#f6f8fc")
        outer = Widget("#ecf2fa")
        content = ScrollContent(outer, (row,))
        outer.children.append(content)
        root = Widget(children=(outer,))
        dimmer = ModalDimmer(root)
        with (
            patch.object(modal_backdrop.ctk, "CTkScrollableFrame", ScrollContent),
            patch.object(modal_backdrop.tk.Frame, "cget",
                         lambda widget, option: widget.native_colors[option]),
            patch.object(modal_backdrop.tk.Frame, "configure",
                         lambda widget, **values: widget.native_colors.update(values)),
        ):
            dimmer.begin()
            for _ in range(3):
                self.assertEqual(content.native_colors["background"], "#6f7071")
                self.assertEqual(row.cget("background"), "#6f7071")
                self.assertEqual(outer.cget("background"), "#6a6d70")
                dimmer.refresh()
            # A newer content update while the popup is open must survive.
            content.native_colors["background"] = "#00ff00"
            dimmer.refresh()
            self.assertEqual(content.native_colors["background"], "#007300")
            dimmer.end()
        self.assertEqual(content.native_colors["background"], "#00ff00")
        self.assertEqual(row.cget("background"), "#f6f8fc")
        self.assertEqual(outer.cget("background"), "#ecf2fa")

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
