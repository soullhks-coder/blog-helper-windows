"""One common in-app dialog for a waiting V2 job's two independent keywords."""
from __future__ import annotations

import copy
import tkinter as tk
import customtkinter as ctk

from input_surfaces import InputEntry
from modal_backdrop import modal_dimmer


class AutomationKeywordEditor(ctk.CTkFrame):
    def __init__(self, app, item):
        palette = app._theme_palette()
        width = min(640, max(360, int(app.main_area.winfo_width() / app.main_area._get_widget_scaling()) - 32))
        super().__init__(app.main_area, width=width, height=410, corner_radius=22,
                         fg_color=palette["shell"], border_width=2, border_color=palette["border"])
        self.app, self.expected = app, copy.deepcopy(item)
        self.item_id, self.closed = str(item["id"]), False
        self.pack_propagate(False)
        ctk.CTkLabel(self, text="대기 키워드 수정", text_color=palette["text"],
                     font=ctk.CTkFont(size=21, weight="bold")).pack(anchor="w", padx=24, pady=(20, 8))
        ctk.CTkLabel(self, text="이 대기열 작업에만 적용합니다. 키워드 관리 원본은 유지됩니다.",
                     text_color=palette["muted"], anchor="w", wraplength=width-48).pack(fill="x", padx=24)
        self.keyword_entry = self.field("글쓰기 키워드", item.get("keyword", ""), palette)
        self.wiki_entry = self.field("위키백과·나무위키 검색어 · 선택", item.get("wiki_keyword", ""), palette,
                                    placeholder="비워 두면 글쓰기 키워드로 검색합니다.")
        self.error = ctk.CTkLabel(self, text="키워드 변경 시 기존 글은 새로 작성하고, 위키 검색어만 변경하면 글은 유지합니다.",
                                  text_color=palette["muted"], anchor="w", justify="left", wraplength=width-48)
        self.error.pack(fill="x", padx=24, pady=(12, 0))
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(side="bottom", fill="x", padx=24, pady=20)
        self.save_button = ctk.CTkButton(actions, text="변경사항 저장", height=36, corner_radius=14,
            fg_color=palette["accent"], text_color="#ffffff", command=self.save)
        self.save_button.pack(side="right")
        self.cancel_button = ctk.CTkButton(actions, text="취소", width=80, height=36, corner_radius=14,
            fg_color=palette["button"], text_color=palette["text"], command=self.close)
        self.cancel_button.pack(side="right", padx=8)
        for entry in (self.keyword_entry, self.wiki_entry):
            entry.bind("<Escape>", lambda _event: self.close())
        self.place(relx=.5, rely=.5, anchor="center")
        app.__dict__.setdefault("_automation_keyword_editing_ids", set()).add(self.item_id)
        app.automation_keyword_dialog = self
        tk.Misc.bind(self, "<Destroy>", self.destroyed, add="+")
        modal_dimmer(app).attach(self)
        self.lift()
        self.grab_set()
        self.keyword_entry.focus_set()

    def field(self, title, value, palette, *, placeholder=""):
        ctk.CTkLabel(self, text=title, text_color=palette["text"], anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(fill="x", padx=24, pady=(12, 4))
        entry = InputEntry(self, height=36, corner_radius=12, border_width=1,
                           fg_color=palette["input"], border_color=palette["border"],
                           text_color=palette["text"], placeholder_text=placeholder)
        entry.pack(fill="x", padx=24)
        if value:
            entry.insert(0, value)
        return entry

    def save(self):
        try:
            self.app._save_automation_queue_keywords(self.item_id, self.expected,
                                                     self.keyword_entry.get(), self.wiki_entry.get())
        except (ValueError, OSError, RuntimeError) as exc:
            self.error.configure(text=str(exc), text_color="#df5252")
            return
        self.close()

    def destroyed(self, event):
        if event.widget is self:
            self.release_hold()

    def release_hold(self):
        self.app.__dict__.get("_automation_keyword_editing_ids", set()).discard(self.item_id)
        if self.app.__dict__.get("automation_keyword_dialog") is self:
            self.app.automation_keyword_dialog = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.grab_release()
        self.destroy()
        modal_dimmer(self.app).detach(self)
        self.release_hold()
        self.app._finish_theme_paint(force=True)
