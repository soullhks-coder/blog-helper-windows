"""Lazy, paginated keyword management using the app's common input surfaces."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tkinter as tk
import uuid
from urllib.parse import urlsplit

import customtkinter as ctk
from input_surfaces import InputEntry, InputOptionMenu, InputTextbox
from keyword_library import KeywordLibrary, read_keyword_file


PLATFORMS = {"wordpress": "워드프레스", "tistory": "티스토리", "blogspot": "블로그스팟",
             "naver_blog": "N블로그", "manual": "직접 사용 처리"}


class KeywordLibraryPage(ctk.CTkFrame):
    PAGE_SIZE = 40

    def __init__(self, master, app, path, dialogs):
        super().__init__(master, fg_color="transparent")
        self.app, self.dialogs = app, dialogs
        self.store = KeywordLibrary(path)
        self.palette = p = app._theme_palette()
        self.history, self.offset = False, 0
        self.group_id, self.move_group_id = None, 0
        self._group_ids = {"전체 그룹": None, "미분류": 0}
        self.rows, self.selection = [], {}
        self._search_job = None
        self._poll_job = None
        self._last_stamp = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=28, pady=(26, 12), sticky="ew")
        ctk.CTkLabel(header, text="키워드 관리", font=ctk.CTkFont(size=28, weight="bold"),
                     text_color=p["text"]).pack(anchor="w")
        self.summary = ctk.CTkLabel(header, text="", text_color=p["muted"], anchor="w")
        self.summary.pack(anchor="w", fill="x", pady=(6, 0))
        self.summary.bind("<Configure>", lambda _event: self.wrap_label(self.summary))

        add_card = ctk.CTkFrame(self, fg_color=p["card"], corner_radius=18)
        add_card.grid(row=1, column=0, padx=28, pady=(0, 12), sticky="ew")
        add_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(add_card, text="키워드 한 번에 등록", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=p["text"]).grid(row=0, column=0, padx=16, pady=(12, 4), sticky="w")
        groups = ctk.CTkFrame(add_card, fg_color="transparent")
        groups.grid(row=1, column=0, columnspan=2, padx=16, pady=(4, 10), sticky="ew")
        groups.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(groups, text="그룹", text_color=p["text"]).grid(row=0, column=0, padx=(0, 8))
        self.group_menu = self.option_menu(groups, values=["전체 그룹", "미분류"], width=180, command=self.change_group)
        self.group_menu.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self.create_group_button = self.button(groups, "+ 그룹 만들기", self.create_group, width=105)
        self.create_group_button.grid(row=0, column=2, padx=(0, 6))
        self.rename_group_button = self.button(groups, "이름 변경", self.rename_group, width=85)
        self.rename_group_button.grid(row=0, column=3, padx=(0, 6))
        self.delete_group_button = self.button(groups, "그룹 삭제", self.delete_group, width=85)
        self.delete_group_button.grid(row=0, column=4)
        self.input = InputTextbox(add_card, height=90, wrap="word")
        self.input.grid(row=2, column=0, padx=16, pady=(0, 6), sticky="ew")
        controls = ctk.CTkFrame(add_card, fg_color="transparent")
        controls.grid(row=2, column=1, padx=(0, 16), sticky="ns")
        self.button(controls, "목록에 추가", self.add_input, primary=True).pack(pady=(0, 8))
        self.button(controls, "TXT · CSV 불러오기", self.import_file).pack()
        ctk.CTkLabel(add_card, text="선택한 그룹에 한 줄씩 등록합니다. 전체 그룹에서는 미분류에 저장하며, 전체 목록 기준 중복·사용 완료는 건너뜁니다.",
                     wraplength=650, justify="left", text_color=p["muted"], anchor="w").grid(
                         row=3, column=0, columnspan=2, padx=16, pady=(0, 12), sticky="ew")

        card = ctk.CTkFrame(self, fg_color=p["card"], corner_radius=18)
        card.grid(row=2, column=0, padx=28, pady=(0, 12), sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)
        tabs = ctk.CTkFrame(card, fg_color="transparent")
        tabs.grid(row=0, column=0, padx=16, pady=12, sticky="ew")
        tabs.grid_columnconfigure(2, weight=1)
        self.pending_tab = self.button(tabs, "대기 키워드", lambda: self.switch(False))
        self.pending_tab.grid(row=0, column=0, padx=(0, 6))
        self.history_tab = self.button(tabs, "사용 이력", lambda: self.switch(True))
        self.history_tab.grid(row=0, column=1, padx=(0, 12))
        self.search = InputEntry(tabs, placeholder_text="키워드 검색")
        self.search.grid(row=0, column=2, sticky="ew")
        self.search.bind("<KeyRelease>", self.search_changed)
        self.clear_search_button = self.button(tabs, "검색 지우기", self.clear_search, width=80)
        self.clear_search_button.grid(row=0, column=3, padx=(8, 0))
        self.button(tabs, "내보내기", self.export_file, width=90).grid(row=0, column=4, padx=(8, 0))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=1, column=0, padx=16, pady=(0, 8), sticky="ew")
        self.select_all = ctk.CTkCheckBox(actions, text="이 페이지 전체", command=self.select_page,
                                         checkbox_width=18, checkbox_height=18, width=130)
        self.select_all.pack(side="left")
        self.used_button = self.button(actions, "사용 처리", self.mark_used, width=90)
        self.used_button.pack(side="left", padx=4)
        self.restore_button = self.button(actions, "대기로 복원", self.restore, width=100)
        self.delete_button = self.button(actions, "선택 삭제", self.remove, width=90)
        self.delete_button.pack(side="left", padx=4)
        move_controls = ctk.CTkFrame(actions, fg_color="transparent")
        move_controls.pack(side="right")
        self.move_group_menu = self.option_menu(move_controls, values=["미분류"], width=150, command=self.change_move_group)
        self.move_group_menu.pack(side="left", padx=(8, 6))
        self.move_group_button = self.button(move_controls, "그룹 이동", self.move_to_group, width=90)
        self.move_group_button.pack(side="left")
        self.list_frame = ctk.CTkScrollableFrame(card, fg_color="transparent", corner_radius=0)
        self.list_frame.grid(row=2, column=0, padx=12, pady=(0, 8), sticky="nsew")
        self.list_frame.grid_columnconfigure(0, weight=1)
        pager = ctk.CTkFrame(card, fg_color="transparent")
        pager.grid(row=3, column=0, padx=16, pady=(0, 12), sticky="ew")
        pager.grid_columnconfigure(0, weight=1)
        self.page_label = ctk.CTkLabel(pager, text="", text_color=p["muted"], anchor="w")
        self.page_label.grid(row=0, column=0, sticky="w")
        self.previous = self.button(pager, "‹ 이전", lambda: self.move(-1), width=75)
        self.previous.grid(row=0, column=1, padx=4)
        self.next = self.button(pager, "다음 ›", lambda: self.move(1), width=75)
        self.next.grid(row=0, column=2)

        footer = ctk.CTkFrame(self, fg_color=p["panel"], corner_radius=16)
        footer.grid(row=3, column=0, padx=28, pady=(0, 14), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(footer, text="발행 성공 시 대기에서 빠지고 이력에 남습니다. 실패·중단·임시저장은 대기에 유지됩니다.",
                                   text_color=p["muted"], anchor="w", wraplength=490, justify="left")
        self.status.grid(row=0, column=0, padx=16, pady=12, sticky="ew")
        self.status.bind("<Configure>", lambda _event: self.wrap_label(self.status))
        self.destination = self.option_menu(footer, values=["블로그 글쓰기", "티스토리 V2"], width=140)
        self.destination.grid(row=0, column=1, padx=8)
        self.write_button = self.button(footer, "선택 키워드 넣기", self.use_for_writing, primary=True, width=140)
        self.write_button.grid(row=0, column=2, padx=(0, 16))
        self.refresh()
        self._poll_job = self.after(1000, self.poll)

    def button(self, parent, text, command, *, primary=False, width=140):
        p = self.palette
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=34,
                             corner_radius=14, fg_color=p["accent"] if primary else p["button"],
                             hover_color=p["button_hover"], text_color="#ffffff" if primary else p["text"])

    def option_menu(self, parent, *, values, width, command=None):
        p = self.palette
        return InputOptionMenu(parent, values=values, width=width, height=32, command=command,
                               dynamic_resizing=False,
                               corner_radius=14, fg_color=p["button"], button_color=p["button"],
                               button_hover_color=p["button_hover"], text_color=p["text"],
                               dropdown_fg_color=p["card"], dropdown_hover_color=p["button_hover"],
                               dropdown_text_color=p["text"])

    def wrap_label(self, label):
        if label.winfo_width() > 40:
            width = max(120, int(label.winfo_width() / label._get_widget_scaling()) - 8)
            if label.cget("wraplength") != width:
                label.configure(wraplength=width)

    def view_state(self):
        return {"input": self.input.get("1.0", "end-1c"), "search": self.search.get(),
                "history": self.history, "offset": self.offset, "destination": self.destination.get(),
                "group_id": self.group_id, "move_group_id": self.move_group_id,
                "selected": [key for key, var in self.selection.items() if var.get()],
                "status": self.status.cget("text")}

    def restore_view_state(self, state):
        self.input.insert("1.0", state["input"])
        if state["search"]:
            self.search.insert(0, state["search"])
        self.destination.set(state["destination"])
        self.group_id = state.get("group_id")
        self.move_group_id = state.get("move_group_id", 0)
        self.switch(state["history"])
        self.offset = state["offset"]
        self.refresh()
        for key in state["selected"]:
            if key in self.selection:
                self.selection[key].set(True)
        self.notice(state["status"])

    def notice(self, text):
        self.status.configure(text=text)

    def refresh_groups(self):
        self._group_ids = {"전체 그룹": None, "미분류": 0,
                           **{row["name"]: row["id"] for row in self.store.groups()}}
        names = {value: key for key, value in self._group_ids.items()}
        if self.group_id not in names:
            self.group_id = None
            self.offset = 0
        if self.move_group_id not in names:
            self.move_group_id = 0
        self.group_menu.configure(values=list(self._group_ids))
        self.group_menu.set(names[self.group_id])
        self.move_group_menu.configure(values=[name for name in self._group_ids if name != "전체 그룹"])
        self.move_group_menu.set(names[self.move_group_id])
        for button in (self.rename_group_button, self.delete_group_button):
            button.configure(state="normal" if self.group_id else "disabled")

    def change_group(self, name):
        self.group_id = self._group_ids.get(name)
        self.clear_search(refresh=False)
        self.refresh()
        self.list_frame._parent_canvas.yview_moveto(0)
        self.notice(f"‘{self.group_menu.get()}’ 목록을 표시합니다. 그룹을 선택한 뒤 키워드를 등록하거나 선택 항목을 이동하세요.")

    def change_move_group(self, name):
        self.move_group_id = self._group_ids.get(name, 0) or 0

    def create_group(self):
        name = self.dialogs["simpledialog"].askstring("그룹 만들기", "새 그룹 이름을 입력해 주세요. (최대 40자)", parent=self.app)
        if name is None:
            return
        group_id = self.safe(lambda: self.store.create_group(name))
        if group_id is not None:
            self.group_id = self.move_group_id = group_id
            self.clear_search(refresh=False)
            self.switch(False)
            self.notice("그룹을 만들었습니다. 위에 키워드를 등록하거나, 전체 그룹의 기존 키워드를 선택해 그룹 이동하세요.")

    def rename_group(self):
        if not self.group_id:
            return
        name = self.dialogs["simpledialog"].askstring("그룹 이름 변경", "그룹 이름을 입력해 주세요. (최대 40자)",
                                                      initialvalue=self.group_menu.get(), parent=self.app)
        if name is not None and self.safe(lambda: (self.store.rename_group(self.group_id, name), True)[1]):
            self.refresh()
            self.notice("그룹 이름을 변경했습니다. 키워드와 사용 이력은 유지됩니다.")

    def delete_group(self):
        if not self.group_id or not self.dialogs["messagebox"].askyesno(
            "그룹 삭제", f"‘{self.group_menu.get()}’ 그룹만 삭제할까요?\n키워드와 사용 이력은 삭제되지 않고 미분류에 남습니다.", parent=self.app,
        ):
            return
        if self.safe(lambda: (self.store.delete_group(self.group_id), True)[1]):
            self.group_id = 0
            self.clear_search(refresh=False)
            self.refresh()
            self.notice("그룹만 삭제했습니다. 해당 키워드와 사용 이력은 미분류에서 확인할 수 있습니다.")

    def move_to_group(self):
        rows = self.selected()
        if not rows:
            return
        count = self.safe(lambda: self.store.assign_group([row["item_id"] for row in rows], self.move_group_id))
        if count is not None:
            name = self.move_group_menu.get()
            self.refresh()
            self.notice(f"{count}개 키워드를 ‘{name}’ 그룹으로 이동했습니다. 기존 사용 이력은 유지됩니다.")

    def safe(self, callback):
        try:
            return callback()
        except ValueError as exc:
            self.dialogs["messagebox"].showwarning("키워드 관리", str(exc), parent=self.app)
            return None
        except Exception as exc:
            self.dialogs["messagebox"].showerror("키워드 관리", f"키워드 작업 중 문제가 생겼습니다. 목록을 다시 열어 저장 상태를 확인해 주세요.\n\n{exc}", parent=self.app)
            return None

    def refresh(self):
        def load():
            self.refresh_groups()
            counts = self.store.counts(group_id=self.group_id)
            rows, total = self.store.list(history=self.history, search=self.search.get(), offset=self.offset,
                                          limit=self.PAGE_SIZE, group_id=self.group_id)
            if not total:
                self.offset = 0
            if total and not rows:
                self.offset = (total - 1) // self.PAGE_SIZE * self.PAGE_SIZE
                rows, total = self.store.list(history=self.history, search=self.search.get(), offset=self.offset,
                                              limit=self.PAGE_SIZE, group_id=self.group_id)
            self.rows, self.selection = rows, {}
            self._last_stamp = self.store.path.stat().st_mtime_ns
            for widget in self.list_frame.winfo_children():
                widget.destroy()
            self.select_all.deselect()
            self.summary.configure(text=f"{self.group_menu.get()}   ·   대기 {counts['pending']:,}개   ·   사용 완료 {counts['used']:,}개   ·   사용 이력 {counts['history']:,}건")
            for index, row in enumerate(rows):
                line = ctk.CTkFrame(self.list_frame, fg_color=self.palette["panel"], corner_radius=12)
                line.grid(row=index, column=0, pady=(0, 6), sticky="ew")
                line.grid_columnconfigure(1, weight=1)
                var = tk.BooleanVar(self, False)
                self.selection[row["id"]] = var
                ctk.CTkCheckBox(line, text="", width=22, checkbox_width=18, checkbox_height=18,
                                variable=var).grid(row=0, column=0, rowspan=2, padx=(12, 4), pady=10)
                label = ctk.CTkLabel(line, text=row["keyword"], text_color=self.palette["text"], anchor="w",
                                     font=ctk.CTkFont(size=14, weight="bold"), wraplength=430, justify="left")
                label.grid(row=0, column=1, padx=8, pady=(9, 0), sticky="w")
                raw_date = row.get("used_at") if self.history else row.get("added_at")
                stamp = datetime.fromisoformat(raw_date).astimezone().strftime("%Y-%m-%d %H:%M")
                detail = (f"{PLATFORMS.get(row['platform'], row['platform'])} · {stamp}"
                          + (" · 대기 복원됨" if row["status"] == "pending" else "")) if self.history else f"등록 {stamp}"
                detail = f"{row['group_name']} · {detail}"
                if self.history and row.get("url"):
                    detail += " · " + urlsplit(row["url"]).netloc
                if self.history and row.get("title"):
                    detail += " · " + row["title"]
                ctk.CTkLabel(line, text=detail, text_color=self.palette["muted"], anchor="w",
                             wraplength=520, justify="left").grid(row=1, column=1, padx=8, pady=(0, 9), sticky="w")
                if self.history and row.get("url", "").startswith(("https://", "http://")):
                    self.button(line, "발행글 보기", lambda url=row["url"]: self.app._open_source_url(url), width=90).grid(
                        row=0, column=2, rowspan=2, padx=12)
            if not rows:
                ctk.CTkLabel(self.list_frame, text="검색 결과가 없습니다. ‘검색 지우기’를 누르면 전체 목록을 볼 수 있습니다." if self.search.get() else
                             "아직 사용한 키워드가 없습니다. 등록한 키워드는 ‘대기 키워드’에서 확인하세요." if self.history else "이 그룹에 키워드가 없습니다. 위에 등록하거나 기존 키워드를 그룹 이동해 주세요.",
                             wraplength=480,
                             text_color=self.palette["muted"]).grid(row=0, column=0, pady=40)
            self.page_label.configure(text=f"{total:,}건 · {self.offset // self.PAGE_SIZE + 1} / {max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)} 페이지")
            self.previous.configure(state="normal" if self.offset else "disabled")
            self.next.configure(state="normal" if self.offset + self.PAGE_SIZE < total else "disabled")
            for button, active, label, count in (
                (self.pending_tab, not self.history, "대기 키워드", counts["pending"]),
                (self.history_tab, self.history, "사용 이력", counts["history"]),
            ):
                button.configure(text=f"{label} {count:,}",
                                 fg_color=self.palette["accent"] if active else self.palette["button"],
                                 text_color="#ffffff" if active else self.palette["text"])
        self.safe(load)

    def add_values(self, values):
        result = self.safe(lambda: self.store.add(values, group_id=self.group_id))
        if result is None:
            return False
        if not any(result[key] for key in ("added", "duplicate", "used")):
            self.notice("추가할 키워드를 한 줄에 하나씩 입력해 주세요.")
            return False
        # Registration must reveal the pending inbox, not leave new entries
        # hidden behind an old history tab, search or pagination/scroll state.
        offset = self.safe(lambda: self.store.pending_page_offset(
            result["first_added_id"], self.PAGE_SIZE, group_id=self.group_id)) or 0
        self.clear_search(refresh=False)
        self.switch(False, offset=offset)
        self.list_frame._parent_canvas.yview_moveto(0)
        self.notice(f"{result['added']:,}개 등록 · 중복 {result['duplicate']:,}개 · 사용 완료 {result['used']:,}개 건너뜀")
        return True

    def add_input(self):
        if self.add_values(self.input.get("1.0", "end").splitlines()):
            self.input.delete("1.0", "end")

    def import_file(self):
        path = self.dialogs["filedialog"].askopenfilename(parent=self.app, title="키워드 불러오기",
            filetypes=[("키워드 목록", "*.txt *.csv"), ("모든 파일", "*.*")])
        if path:
            values = self.safe(lambda: read_keyword_file(Path(path)))
            if values is not None:
                self.add_values(values)

    def export_file(self):
        path = self.dialogs["filedialog"].asksaveasfilename(parent=self.app, title="키워드 내보내기",
            initialfile="키워드-사용이력.csv" if self.history else "키워드-대기목록.txt",
            defaultextension=".csv" if self.history else ".txt", filetypes=[("CSV", "*.csv"), ("TXT", "*.txt")])
        if path and self.safe(lambda: (self.store.export(Path(path), history=self.history, group_id=self.group_id), True)):
            self.notice("검색·페이지와 관계없이 선택한 그룹의 현재 탭 전체 목록을 내보냈습니다. CSV에는 그룹 이름도 포함됩니다.")

    def switch(self, history, *, offset=0):
        self.history, self.offset = history, offset
        self.used_button.pack_forget()
        self.delete_button.pack_forget()
        self.restore_button.pack_forget()
        if history:
            self.restore_button.pack(side="left", padx=4)
        else:
            self.used_button.pack(side="left", padx=4)
            self.delete_button.pack(side="left", padx=4)
        self.write_button.configure(state="disabled" if history else "normal")
        self.refresh()

    def clear_search(self, *, refresh=True):
        if self._search_job is not None:
            self.after_cancel(self._search_job)
            self._search_job = None
        self.search.delete(0, "end")
        self.offset = 0
        if refresh:
            self.refresh()
            self.list_frame._parent_canvas.yview_moveto(0)

    def search_changed(self, _event=None):
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(240, self.apply_search)

    def apply_search(self):
        self._search_job = None
        self.offset = 0
        self.refresh()

    def poll(self):
        self._poll_job = None
        if self.app.current_page == "keywords":
            try:
                changed = self.store.path.stat().st_mtime_ns != self._last_stamp
            except OSError:
                changed = False
            if changed:
                self.refresh()
                self.notice("발행 결과를 반영해 키워드 목록과 사용 이력을 갱신했습니다.")
        self._poll_job = self.after(1000, self.poll)

    def destroy(self):
        for name in ("_search_job", "_poll_job"):
            job = getattr(self, name, None)
            if job is not None:
                self.after_cancel(job)
                setattr(self, name, None)
        super().destroy()

    def move(self, direction):
        self.offset = max(0, self.offset + direction * self.PAGE_SIZE)
        self.refresh()
        self.list_frame._parent_canvas.yview_moveto(0)

    def select_page(self):
        for variable in self.selection.values():
            variable.set(bool(self.select_all.get()))

    def selected(self):
        rows = [row for row in self.rows if self.selection[row["id"]].get()]
        if not rows:
            self.notice("먼저 목록에서 키워드를 선택해 주세요.")
        return rows

    def mark_used(self):
        rows = self.selected()
        if not rows or not self.dialogs["messagebox"].askyesno("사용 처리", f"선택한 {len(rows)}개를 사용 이력으로 옮길까요?\n발행 없이 직접 처리하며, 이력에서 다시 복원할 수 있습니다.", parent=self.app):
            return
        def update():
            for row in rows:
                self.store.record_use(row["keyword"], platform="manual", event_key="manual:" + uuid.uuid4().hex)
            return True
        if self.safe(update):
            self.notice(f"{len(rows)}개를 사용 이력으로 옮겼습니다.")
            self.refresh()

    def restore(self):
        rows = self.selected()
        if rows:
            count = self.safe(lambda: self.store.change_status([row["item_id"] for row in rows], "pending"))
            if count is not None:
                self.notice(f"{count}개를 대기로 복원했습니다. 기존 사용 이력은 보존됩니다.")
                self.refresh()

    def remove(self):
        rows = self.selected()
        if rows and self.dialogs["messagebox"].askyesno("대기 키워드 삭제", f"선택한 {len(rows)}개를 대기 목록에서 삭제할까요?\n기존 사용 이력은 지우지 않습니다.", parent=self.app):
            count = self.safe(lambda: self.store.change_status([row["item_id"] for row in rows], "removed"))
            if count is not None:
                self.notice(f"대기 목록에서 {count}개를 삭제했습니다. 같은 키워드를 다시 등록할 수 있습니다.")
                self.refresh()

    def use_for_writing(self):
        rows = self.selected()
        if len(rows) != 1:
            self.notice("글쓰기에 넣을 키워드를 하나만 선택해 주세요.")
            return
        destination = "tistory_v2" if self.destination.get() == "티스토리 V2" else "writing"
        self.app._use_managed_keyword(rows[0]["keyword"], destination)
