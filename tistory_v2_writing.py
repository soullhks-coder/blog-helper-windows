"""V2 selectors for the shared four-stage writing screen (no separate workflow)."""
from __future__ import annotations

import copy
from pathlib import Path
from tkinter import messagebox

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
        self.grid_columnconfigure(2, weight=1)
        self.platform_var = ctk.StringVar(value=self.values.get("platform", "tistory"))
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
        blogs = ctk.CTkFrame(self, fg_color="transparent")
        blogs.grid(row=0, column=1, sticky="w")
        self.platform_buttons = {}
        for column, platform in enumerate(("tistory", "blogspot")):
            group = ctk.CTkFrame(blogs, fg_color="transparent")
            group.grid(row=0, column=column, padx=(0, 10), sticky="w")
            radio = ctk.CTkRadioButton(
                group, text="", width=20, height=22, radiobutton_width=18, radiobutton_height=18,
                variable=self.platform_var, value=platform,
                command=lambda: self.changed("platform"))
            radio.grid(row=0, column=0, padx=(0, 4))
            self.platform_buttons[platform] = radio
            logo = app._home_platform_compact_logo(platform)
            ctk.CTkLabel(group, text="" if logo else ("티스토리" if platform == "tistory" else "블로그스팟"),
                         image=logo, width=22, height=22).grid(row=0, column=1, padx=(0, 4))
            key = "profile" if platform == "tistory" else "blogspot_profile"
            self.menus[key] = self._menu(group, key, palette, width=122)
            self.menus[key].grid(row=0, column=2)
        for column, key, label in ((3, "thumbnail", "썸네일"), (5, "prompt_id", "프롬프트")):
            ctk.CTkLabel(self, text=label, text_color=palette["muted"],
                         font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=column, padx=(8, 5))
            menu = self._menu(self, key, palette, width=106 if key == "thumbnail" else 140)
            menu.grid(row=0, column=column + 1, sticky="e")
            self.menus[key] = menu
        self.refresh()

    def _menu(self, master, key, palette, *, width):
        return InputOptionMenu(
            master, values=["불러오는 중"], width=width,
            height=27, corner_radius=10, fg_color=palette["button"],
            button_color=palette["button"], button_hover_color=palette["button_hover"],
            dropdown_fg_color=palette["panel"], dropdown_hover_color=palette["hover"],
            dropdown_text_color=palette["text"], text_color=palette["text"],
            font=ctk.CTkFont(size=11, weight="bold"),
            command=lambda _value, field=key: self.changed(field),
        )

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
        platform = self.values.get("platform", "tistory")
        if platform not in {"tistory", "blogspot"}:
            platform = "tistory"
        self.platform_var.set(platform)
        profiles = self.values.get("profile_selections", {})
        for target, key in (("tistory", "profile"), ("blogspot", "blogspot_profile")):
            options = self.workflow.options(target)
            choices = {item["label"]: item["id"] for item in options["profiles"]}
            self.choices[key] = choices
            wanted = profiles.get(target, self.values.get("profile") if target == platform else None)
            wanted = wanted or options["active_profile"]
            label = next((label for label, value in choices.items() if value == wanted), next(iter(choices), "블로그 미등록"))
            self.menus[key].configure(values=list(choices) or [label])
            self.menus[key].set(label)
        options = self.workflow.options(platform)
        self.choices.update({
            "prompt_id": {item["label"]: item["id"] for item in options["prompts"]},
            "thumbnail": {label: index for index, label in enumerate(options["thumbnails"])},
        })
        defaults = {"profile": options["active_profile"], "thumbnail": options["thumbnail"]}
        for key, menu in self.menus.items():
            if key in {"profile", "blogspot_profile"}:
                continue
            choices = self.choices[key]
            wanted = self.values.get(key, defaults.get(key))
            label = next((label for label, value in choices.items() if value == wanted), next(iter(choices), "등록된 항목 없음"))
            menu.configure(values=list(choices) or [label])
            menu.set(label)
        self.values.update(self.selection())

    def selection(self):
        platform = self.platform_var.get()
        key = "blogspot_profile" if platform == "blogspot" else "profile"
        return {"platform": platform, "profile": self.choices[key].get(self.menus[key].get()),
                **{field: self.choices[field].get(self.menus[field].get()) for field in ("thumbnail", "prompt_id")}}

    def changed(self, key):
        active = self.app._sidebar_activity_map()
        panel = self.app.__dict__.get("tistory_v2_panel")
        scope = self.app.__dict__.get("_writing_run_queue")
        pending = False
        if scope is not None:
            with self.app.result_queue.mutex:
                pending = any(event[0] == "writing_run_event" and event[1][0] is scope
                              for event in self.app.result_queue.queue)
        if pending or active.get("tistory_v2") or active.get("writing") or (panel is not None and panel.busy):
            self.refresh()
            messagebox.showinfo("글쓰기 진행 중", "현재 작업을 마치거나 발행중단 후 블로그 설정을 변경해 주세요.", parent=self.app)
            return
        # The embedded slide editor owns deck/theme fields in this same file.
        self.values = {**self.values, **self.store.preferences()}
        preferences = self.values.setdefault("blog_preferences", {})
        previous_platform = self.values.get("platform", "tistory")
        previous_profile = self.values.get("profile")
        if previous_profile:
            preferences[f"{previous_platform}:{previous_profile}"] = {
                field: self.values.get(field) for field in ("thumbnail", "prompt_id")}
        if key in {"profile", "blogspot_profile"}:
            self.platform_var.set("blogspot" if key == "blogspot_profile" else "tistory")
        selection = self.selection()
        profiles = self.values.setdefault("profile_selections", {})
        for target, field in (("tistory", "profile"), ("blogspot", "blogspot_profile")):
            profiles[target] = self.choices[field].get(self.menus[field].get())
        if key in {"platform", "profile", "blogspot_profile"}:
            identity = f"{selection['platform']}:{selection['profile']}"
            remembered = preferences.get(identity, preferences.get(str(selection["profile"]), {}))
            self.values.update(platform=selection["platform"], profile=selection["profile"], prompt_id=None,
                               thumbnail=self.workflow.options(selection["platform"])["thumbnail"])
            self.values.update({k: remembered[k] for k in ("thumbnail", "prompt_id") if k in remembered})
            self.refresh()
            selection = self.selection()
        self.values.update(selection)
        preferences[f"{selection['platform']}:{selection['profile']}"] = {k: selection[k] for k in ("thumbnail", "prompt_id")}
        self.store.save_preferences(self.values)
        if key in {"thumbnail", "platform", "profile", "blogspot_profile"}:
            self.app._switch_thumbnail_preset(selection["thumbnail"] or 0, save=False)
        self.app.tistory_retry_button.configure(text=("블로그스팟 자동입력" if selection["platform"] == "blogspot" else "티스토리 자동입력"))
        self.app._layout_writing_control_row()

    def select_thumbnail(self, index):
        label = next(label for label, value in self.choices["thumbnail"].items() if value == index)
        self.menus["thumbnail"].set(label)
        self.changed("thumbnail")

    def execution_settings(self, base):
        """Override only the detached run: never change normal/home saved targets."""
        selection = self.selection()
        platform = selection.get("platform", "tistory")
        if platform not in {"tistory", "blogspot"}:
            raise ValueError("V2에서 지원하지 않는 블로그입니다.")
        label = "블로그스팟" if platform == "blogspot" else "티스토리"
        prompt = next((p for p in self.app._prompt_sets() if p.get("platform") == platform
                       and p.get("id") == selection["prompt_id"]), None)
        normalize = self.api.normalize_blogspot_profiles if platform == "blogspot" else self.api.normalize_tistory_profiles
        profile = next((p for p in normalize(getattr(base, platform + "_profiles"))
                        if p["name"] == selection["profile"]), None)
        if not prompt:
            raise ValueError(f"사용할 {label} 글쓰기 프롬프트를 선택해 주세요.")
        if not profile:
            raise ValueError(f"환경설정에 {label} 블로그를 등록해 주세요.")
        settings = copy.deepcopy(base)
        settings.target_platforms = [platform]
        settings.writing_prompt_active_target = platform
        settings.selected_prompt_id = prompt["id"]
        settings.title_prompt_template = prompt["title_prompt"]
        settings.article_prompt_template = prompt["article_prompt"]
        settings.inline_images_enabled = False
        setattr(settings, platform + "_active_profile", profile["name"])
        fields = (("blog_id", "blog_url", "daily_publish_limit", "auto_publish", "save_mode", "reference_image_protection_mode")
                  if platform == "blogspot" else
                  ("blog_url", "write_url", "daily_publish_limit", "input_mode", "auto_publish", "upload_webp",
                   "save_mode", "reference_image_protection_mode", "ads_enabled", "ads_code", "ads_slot_id", "ads_position", "ads_count"))
        for field in fields:
            setattr(settings, platform + "_" + field, profile[field])
        if self.review_publish_var.get():
            # Reuse the existing native manual-review boundary: input the
            # thumbnail/body/collages/tags, then keep Chrome open waiting for
            # the user's publish + completion confirmation. Never change the
            # shared blog profile or normal-writing auto-publish preference.
            setattr(settings, platform + "_auto_publish", False)
            setattr(settings, platform + "_save_mode", self.api.TISTORY_SAVE_MODE_PUBLISH)
        return settings

    def export_thumbnail(self, title, destination: Path):
        selection = self.selection()
        spec = self.workflow._thumbnail_svg(selection["thumbnail"] or 0, title)
        self.api.render_svg_to_png(spec["svg"], destination, spec["size"], "티스토리 V2 썸네일")
        return destination
