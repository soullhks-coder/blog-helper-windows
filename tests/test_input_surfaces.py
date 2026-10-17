import ast
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import customtkinter as ctk

from input_surfaces import InputComboBox, InputEntry, InputOptionMenu, entry_vertical_inset, outline_width


class InputSurfaceTests(unittest.TestCase):
    def test_flat_or_thin_legacy_inputs_keep_a_visible_outline(self):
        for value in (None, 0, 1, 1.5, 2):
            self.assertEqual(outline_width(value), 2)
        self.assertEqual(outline_width(3), 3)

    def test_insets_round_up_instead_of_truncating_fractional_scaling(self):
        for scale in (.65, .82, 1, 1.25, 1.5, 2):
            self.assertGreater(entry_vertical_inset(2, scale), 2 * scale)
        self.assertEqual(entry_vertical_inset(2, .82), 3)

    def test_native_editor_is_centered_after_every_grid_rebuild(self):
        editor = Mock()
        widget = object.__new__(InputEntry)
        widget._entry = editor
        widget._border_width = 2
        widget._get_widget_scaling = lambda: .82
        with patch.object(ctk.CTkEntry, '_create_grid') as base:
            InputEntry._create_grid(widget)
        base.assert_called_once()
        editor.grid_configure.assert_called_once_with(sticky='ew', pady=(3, 3))

    def test_all_application_input_constructors_use_shared_components(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / 'main.py', root / 'tistory_v2_ui.py', root / 'tistory_v2_writing.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            constructors = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
            self.assertFalse([node for node in constructors if isinstance(node, ast.Attribute)
                              and isinstance(node.value, ast.Name) and node.value.id == 'ctk'
                              and node.attr in {'CTkEntry', 'CTkTextbox', 'CTkOptionMenu', 'CTkComboBox'}], path.name)

    def test_option_menu_exposes_and_enforces_shared_outline_options(self):
        menu = object.__new__(InputOptionMenu)
        menu._create_grid = Mock()
        with patch.object(ctk.CTkOptionMenu, 'configure') as base:
            menu.configure(border_width=0, border_color='#123456', state='disabled')
        self.assertEqual(menu.cget('border_width'), 2)
        self.assertEqual(menu.cget('border_color'), '#123456')
        menu._create_grid.assert_called_once()
        base.assert_called_once_with(require_redraw=True, state='disabled')

    def test_dropdown_grid_rebuild_keeps_native_text_inside_outline(self):
        for cls, base, native_name in (
            (InputOptionMenu, ctk.CTkOptionMenu, '_text_label'),
            (InputComboBox, ctk.CTkComboBox, '_entry'),
        ):
            widget = object.__new__(cls)
            native = Mock()
            setattr(widget, native_name, native)
            widget._border_width = 2
            widget._get_widget_scaling = lambda: .82
            with patch.object(base, '_create_grid') as grid:
                widget._create_grid()
            grid.assert_called_once()
            native.grid_configure.assert_called_once_with(pady=(3, 3))


if __name__ == '__main__':
    unittest.main()
