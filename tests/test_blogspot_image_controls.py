import unittest
from unittest.mock import Mock

from playwright.sync_api import Error as PlaywrightError
from blogspot_image_controls import find_size_choice, select_popup_size


def popup(*, ignore=0, stay_open=False, checked=True):
    state = {"clicks": 0, "open": True}
    events = []
    item = Mock()
    item.is_visible.side_effect = lambda: state["open"]
    item.get_attribute.side_effect = lambda name: "true" if name == "aria-checked" and state["clicks"] > ignore and checked else "false"
    item.bounding_box.return_value = {"width": 128, "height": 40}
    item.hover.side_effect = lambda **_kwargs: events.append("hover")
    def click(**kwargs):
        state["clicks"] += 1
        events.append(("click", kwargs))
        if state["clicks"] > ignore:
            state["open"] = stay_open
    item.click.side_effect = click
    found, absent = Mock(), Mock()
    found.count.return_value = 1
    found.nth.return_value = item
    absent.count.return_value = 0
    page = Mock()
    page.is_closed.return_value = False
    page.get_by_role.side_effect = lambda role, name, exact: found if role == "menuitemradio" and name == "매우 크게" else absent
    page.get_by_text.return_value = absent
    page.wait_for_timeout.side_effect = lambda delay: events.append(("wait", delay))
    return page, item, state, events


class BloggerSizeMenuTests(unittest.TestCase):
    def test_semantic_menu_row_gets_hover_held_click_and_post_click_wait(self):
        page, item, state, events = popup()
        select_popup_size(page)
        self.assertEqual(state["clicks"], 1)
        self.assertEqual(events, ["hover", ("wait", 180),
            ("click", {"position": {"x": 12, "y": 12}, "delay": 120, "timeout": 4000}), ("wait", 1500)])
        page.get_by_text.assert_not_called()

    def test_ignored_first_click_retries_only_the_same_open_menu_once(self):
        page, item, state, events = popup(ignore=1)
        log = Mock()
        select_popup_size(page, log=log)
        self.assertEqual(state["clicks"], 2)
        self.assertEqual([entry for entry in events if entry == ("wait", 1500)], [("wait", 1500)] * 2)
        self.assertTrue(any("다시 누릅니다" in call.args[0] for call in log.call_args_list))

    def test_checked_item_does_not_retry_while_image_can_still_be_loading(self):
        page, item, state, _events = popup(stay_open=True)
        select_popup_size(page)
        self.assertEqual(state["clicks"], 1)

    def test_two_unaccepted_clicks_stop_without_claiming_a_success(self):
        page, item, state, _events = popup(ignore=99)
        with self.assertRaisesRegex(RuntimeError, "선택이 접수되지"):
            select_popup_size(page)
        self.assertEqual(state["clicks"], 2)

    def test_text_fallback_resolves_its_ancestor_menu_row(self):
        page, item, state, _events = popup()
        empty = Mock()
        empty.count.return_value = 0
        page.get_by_role.return_value = empty
        page.get_by_role.side_effect = None
        label, labels, parent = Mock(), Mock(), Mock()
        labels.count.return_value = 1
        labels.nth.return_value = label
        label.is_visible.return_value = True
        label.locator.return_value = parent
        parent.count.return_value = 1
        parent.nth.return_value = item
        page.get_by_text.return_value = labels
        select_popup_size(page)
        label.click.assert_not_called()
        item.click.assert_called_once()

    def test_closed_browser_does_not_retry(self):
        page, item, _state, _events = popup()
        item.hover.side_effect = PlaywrightError("closed")
        page.is_closed.return_value = True
        with self.assertRaisesRegex(RuntimeError, "브라우저가 닫혔"):
            select_popup_size(page)
        item.click.assert_not_called()

    def test_missing_menu_choice_fails_without_pointer_input(self):
        page, item, _state, _events = popup()
        empty = Mock()
        empty.count.return_value = 0
        page.get_by_role.side_effect = None
        page.get_by_role.return_value = empty
        with self.assertRaisesRegex(RuntimeError, "찾지 못했"):
            select_popup_size(page)
        item.click.assert_not_called()
