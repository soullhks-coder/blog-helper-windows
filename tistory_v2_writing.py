"""V2 selectors for the shared four-stage writing screen (no separate workflow)."""
from __future__ import annotations

import copy
from pathlib import Path

import customtkinter as ctk
from input_surfaces import InputOptionMenu

from tistory_v2 import V2Store
from tistory_v2_workflow import TistoryV2Workflow


class TistoryV2WritingControls(ctk.CTkFrame):
    def __init__(self, master, app, api):
        super().__init__(master, fg_color="transparent")
        self.app, self.api = app, api
        self.workflow = TistoryV2Workflow(app, api)
        self.store = V2Store(api.DATA_DIR / "Tistory V2")
        self.values = self.store.preferences()
        self.menus, self.choices = {}, {}
        palette = app._theme_palette()
        self.grid_columnconfigure(3, weight=1)
        self.review_publish_var = ctk.BooleanVar(value=bool(self.values.get("review_publish", False)))
        self.review_publish_switch = ctk.CTkSwitch(
            self, text="검토 발행 OFF", variable=self.review_publish_var,
            onvalue=True, offvalue=False, switch_width=50, switch_height=26,
            corner_radius=13, progress_color="#1faa55",
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._on_review_publish_toggled,
        )
        self.review_publish_switch.grid(row=0, column=0, padx=(0, 12), sticky="w")
        self._refresh_review_publish_ui()
        logo = app._home_platform_compact_logo("tistory")
        ctk.CTkLabel(self, text="" if logo else "티스토리", image=logo,
                     width=22, height=22).grid(row=0, column=1, padx=(0, 5))
        for column, key, label in ((2, "profile", ""), (4, "thumbnail", "썸네일"), (6, "prompt_id", "프롬프트")):
            if label:
                ctk.CTkLabel(self, text=label, text_color=palette["muted"],
                             font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=column, padx=(8, 5))
            menu = InputOptionMenu(
                self, values=["불러오는 중"], width=140 if key != "thumbnail" else 106,
                height=27, corner_radius=10, fg_color=palette["button"],
                button_color=palette["button"], button_hover_color=palette["button_hover"],
                dropdown_fg_color=palette["panel"], dropdown_hover_color=palette["hover"],
                dropdown_text_color=palette["text"], text_color=palette["text"],
                font=ctk.CTkFont(size=11, weight="bold"),
                command=lambda _value, field=key: self.changed(field),
            )
            menu.grid(row=0, column=column + (1 if label else 0), sticky="e")
            self.menus[key] = menu
        # Column 3 is the expanding spacer after the blog selector.
        self.refresh()

    def _refresh_review_publish_ui(self):
        self.review_publish_switch.configure(
            text=f"검토 발행 {'ON' if self.review_publish_var.get() else 'OFF'}")

    def _on_review_publish_toggled(self):
        # Merge current slide preferences instead of overwriting the editor's
        # newer deck/theme changes with this header's cached values.
        self.values = {**self.values, **self.store.preferences(),
                       "review_publish": bool(self.review_publish_var.get())}
        self.store.save_preferences(self.values)
        self._refresh_review_publish_ui()

    def refresh(self):
        options = self.workflow.options()
        self.choices = {
            "profile": {item["label"]: item["id"] for item in options["profiles"]},
            "prompt_id": {item["label"]: item["id"] for item in options["prompts"]},
            "thumbnail": {label: index for index, label in enumerate(options["thumbnails"])},
        }
        defaults = {"profile": options["active_profile"], "thumbnail": options["thumbnail"]}
        for key, menu in self.menus.items():
            choices = self.choices[key]
            wanted = self.values.get(key, defaults.get(key))
            label = next((label for label, value in choices.items() if value == wanted), next(iter(choices), "등록된 항목 없음"))
            menu.configure(values=list(choices) or [label])
            menu.set(label)
        self.values.update(self.selection())

    def selection(self):
        return {key: self.choices[key].get(menu.get()) for key, menu in self.menus.items()}

    def changed(self, key):
        selection = self.selection()
        # The embedded slide editor owns deck/theme fields in this same file.
        self.values = {**self.values, **self.store.preferences()}
        preferences = self.values.setdefault("blog_preferences", {})
        if key == "profile":
            remembered = preferences.get(str(selection["profile"]), {})
            self.values.update(selection)
            self.values.update({k: remembered[k] for k in ("thumbnail", "prompt_id") if k in remembered})
            self.refresh()
            selection = self.selection()
        self.values.update(selection)
        preferences[str(selection["profile"])] = {k: selection[k] for k in ("thumbnail", "prompt_id")}
        self.store.save_preferences(self.values)
        if key in {"thumbnail", "profile"}:
            self.app._switch_thumbnail_preset(selection["thumbnail"] or 0, save=False)

    def select_thumbnail(self, index):
        label = next(label for label, value in self.choices["thumbnail"].items() if value == index)
        self.menus["thumbnail"].set(label)
        self.changed("thumbnail")

    def execution_settings(self, base):
        """Override only the detached run: never change normal/home saved targets."""
        selection = self.selection()
        prompt = next((p for p in self.app._prompt_sets() if p.get("platform") == "tistory"
                       and p.get("id") == selection["prompt_id"]), None)
        profile = next((p for p in self.api.normalize_tistory_profiles(base.tistory_profiles)
                        if p["name"] == selection["profile"]), None)
        if not prompt:
            raise ValueError("사용할 티스토리 글쓰기 프롬프트를 선택해 주세요.")
        if not profile:
            raise ValueError("환경설정에 티스토리 블로그를 등록해 주세요.")
        settings = copy.deepcopy(base)
        settings.target_platforms = ["tistory"]
        settings.writing_prompt_active_target = "tistory"
        settings.selected_prompt_id = prompt["id"]
        settings.title_prompt_template = prompt["title_prompt"]
        settings.article_prompt_template = prompt["article_prompt"]
        settings.inline_images_enabled = False
        settings.tistory_active_profile = profile["name"]
        for field in ("blog_url", "write_url", "daily_publish_limit", "input_mode", "auto_publish", "upload_webp",
                      "save_mode", "reference_image_protection_mode", "ads_enabled", "ads_code",
                      "ads_slot_id", "ads_position", "ads_count"):
            setattr(settings, "tistory_" + field, profile[field])
        if self.review_publish_var.get():
            # Reuse the existing native manual-review boundary: input the
            # thumbnail/body/collages/tags, then keep Chrome open waiting for
            # the user's publish + completion confirmation. Never change the
            # shared blog profile or normal-writing auto-publish preference.
            settings.tistory_auto_publish = False
            settings.tistory_save_mode = self.api.TISTORY_SAVE_MODE_PUBLISH
        return settings

    def export_thumbnail(self, title, destination: Path):
        selection = self.selection()
        spec = self.workflow._thumbnail_svg(selection["thumbnail"] or 0, title)
        self.api.render_svg_to_png(spec["svg"], destination, spec["size"], "티스토리 V2 썸네일")
        return destination
