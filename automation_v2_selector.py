"""Compact, independently saved destination for newly queued V2 jobs."""
from tkinter import messagebox

import customtkinter as ctk

from automation_v2 import selected_profile
from input_surfaces import InputOptionMenu
from tistory_v2 import V2Store


class AutomationV2BlogSelector(ctk.CTkFrame):
    def __init__(self, master, app, api):
        super().__init__(master, fg_color="transparent")
        self.app, self.api = app, api
        self.choices = {}
        palette = app._theme_palette()
        self.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(self, text="V2 발행 블로그", text_color=palette["text"],
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
                         row=0, column=0, padx=(0, 10), sticky="w")
        logo = app._home_platform_compact_logo("tistory")
        ctk.CTkLabel(self, text="" if logo else "T", image=logo, width=22, height=22).grid(
            row=0, column=1, padx=(0, 5))
        self.menu = InputOptionMenu(
            self, values=["블로그 미등록"], width=200, height=28, corner_radius=10,
            fg_color=palette["button"], button_color=palette["button"],
            button_hover_color=palette["button_hover"], text_color=palette["text"],
            dropdown_fg_color=palette["panel"], dropdown_hover_color=palette["hover"],
            dropdown_text_color=palette["text"], font=ctk.CTkFont(size=12, weight="bold"),
            command=self.changed,
        )
        self.menu.grid(row=0, column=2, sticky="w")
        ctk.CTkLabel(self, text="새로 등록할 V2 작업에 적용", text_color=palette["muted"],
                     font=ctk.CTkFont(size=12)).grid(row=0, column=3, padx=(10, 0), sticky="w")
        self.refresh()

    def refresh(self):
        settings, api = self.app.wordpress_settings, self.api
        valid = {p["name"] for p in api.normalize_tistory_profiles(settings.tistory_profiles)
                 if api.build_tistory_write_url(p["blog_url"], p["write_url"])}
        self.choices = {label: name for name, label in api.home_blog_choices("tistory", settings)
                        if name in valid}
        values = V2Store(api.DATA_DIR / "Tistory V2").preferences()
        wanted = selected_profile(settings, values)
        label = next((label for label, name in self.choices.items() if name == wanted), None)
        if label is None:
            label = "선택한 블로그 연결 확인" if self.choices else "블로그 미등록"
        self.menu.configure(values=list(self.choices) or [label],
                            state="normal" if self.choices else "disabled")
        self.menu.set(label)

    def changed(self, label):
        name = self.choices.get(label)
        if not name:
            return
        settings = self.app.wordpress_settings
        previous = settings.automation_v2_profile
        try:
            if getattr(settings, "_state_read_error", ""):
                raise ValueError("설정 파일을 복구한 뒤 발행 블로그를 변경해 주세요.")
            settings.automation_v2_profile = name
            self.api.AppStateStore.save(settings, save_secrets=False)
        except Exception as exc:
            settings.automation_v2_profile = previous
            self.refresh()
            messagebox.showerror("V2 발행 블로그 저장 실패", str(exc), parent=self.app)
            return
        self.refresh()
