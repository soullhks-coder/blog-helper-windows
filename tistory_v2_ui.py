"""Lazy, independent slide-image workspace for the Tistory V2 menu."""
from __future__ import annotations

import copy
import queue
import shutil
import threading
import tkinter as tk
import uuid
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

from modal_backdrop import DimmedDialogApi
from tistory_v2 import (COPYRIGHT_NOTE, THEMES, Cancelled, V2Store, collect_wiki_images,
                        export_deck, plain_text, render_slide)

filedialog = DimmedDialogApi(filedialog)
messagebox = DimmedDialogApi(messagebox)


class TistoryV2Panel(ctk.CTkFrame):
    def __init__(self, master, app, directory: Path, chrome_path_provider, apply_images):
        super().__init__(master, fg_color="transparent")
        self._uses_writing_design_theme = True
        # Map the lazy page before CTk dropdowns run their idle geometry work.
        self.grid(row=0, column=0, sticky="nsew")
        self.app = app
        self.palette = app._theme_palette()
        self.store = V2Store(directory)
        self.chrome_path_provider = chrome_path_provider
        self.apply_images = apply_images
        self.deck = None
        self.slide_index = 0
        self.worker = None
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self._poll_job = self._edit_job = self._resize_job = None
        self._closed = False
        self._controls = []
        self._preview_image = None
        self._deck_labels = {}
        self._image_labels = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        values = self.store.preferences()
        self.mode = tk.StringVar(value=values.get("mode") if values.get("mode") in {"자동 모드", "수동 모드"} else "자동 모드")
        self.theme = tk.StringVar(value=values.get("theme") if values.get("theme") in THEMES else "스카이")
        self.count = tk.StringVar(value=str(values.get("count", 10)) if str(values.get("count", 10)) in {"1", "3", "5", "10"} else "10")
        self.show_browser = tk.BooleanVar(value=bool(values.get("show_browser", True)))
        self._build(values)
        self._refresh_deck_menu()
        saved_id = values.get("deck_id", "")
        if saved_id:
            try:
                self._load_deck(self.store.load_deck(saved_id))
            except (OSError, ValueError):
                self.status.configure(text="마지막 작업 파일을 찾지 못했습니다. 새 작업을 만들거나 저장 목록에서 선택해 주세요.")
        self._poll_job = self.after(150, self._poll)

    def _label(self, parent, text, row, column=0):
        label = ctk.CTkLabel(parent, text=text, anchor="w", font=ctk.CTkFont(size=14, weight="bold"), text_color=self.palette["text"])
        label.grid(row=row, column=column, padx=18, pady=(14, 6), sticky="ew")
        return label

    def _entry(self, parent, row, placeholder=""):
        entry = ctk.CTkEntry(parent, height=38, fg_color=self.palette["input"], border_color=self.palette["border"],
                             text_color=self.palette["text"], placeholder_text_color=self.palette["muted"],
                             placeholder_text=placeholder)
        entry.grid(row=row, column=0, padx=18, sticky="ew")
        self._controls.append(entry)
        return entry

    def _menu(self, parent, row, values, variable=None, command=None):
        menu = ctk.CTkOptionMenu(parent, values=values, variable=variable, height=34,
                                 fg_color=self.palette["button"], button_color=self.palette["button"],
                                 button_hover_color=self.palette["button_hover"], text_color=self.palette["text"],
                                 dropdown_fg_color=self.palette["panel"], dropdown_hover_color=self.palette["hover"],
                                 dropdown_text_color=self.palette["text"], command=command)
        menu.grid(row=row, column=0, padx=18, sticky="ew")
        self._controls.append(menu)
        return menu

    def _button(self, parent, text, command, row, column=0, primary=False):
        button = ctk.CTkButton(parent, text=text, height=36, width=90, corner_radius=12, command=command,
                               fg_color="#2563eb" if primary else self.palette["button"],
                               hover_color="#1d4ed8" if primary else self.palette["button_hover"],
                               text_color="#ffffff" if primary else self.palette["text"])
        button.grid(row=row, column=column, padx=5, pady=5, sticky="ew")
        self._controls.append(button)
        return button

    def _textbox(self, parent, row, height):
        box = ctk.CTkTextbox(parent, height=height, fg_color=self.palette["input"], border_width=1,
                             border_color=self.palette["border"], text_color=self.palette["text"],
                             corner_radius=12, font=ctk.CTkFont(size=14))
        box.grid(row=row, column=0, padx=18, sticky="ew")
        self._controls.append(box)
        return box

    def _build(self, values):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=28, pady=(26, 14), sticky="ew")
        ctk.CTkLabel(header, text="티스토리 V2", font=ctk.CTkFont(size=30, weight="bold"), text_color=self.palette["text"]).pack(anchor="w")
        self.intro_label = ctk.CTkLabel(header, text="위키 사진으로 만드는 PPT 스타일 본문 이미지 · 기존 티스토리 기능과 별도 관리",
                                      width=1, wraplength=400, anchor="w", justify="left",
                                      text_color=self.palette["muted"], font=ctk.CTkFont(size=13))
        self.intro_label.pack(fill="x", pady=(5, 0))
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", scrollbar_button_color=self.palette["divider"])
        self.scroll.grid(row=1, column=0, padx=28, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)
        self.workspace = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.workspace.grid(row=0, column=0, sticky="ew")
        self.workspace.bind("<Configure>", self._layout, add="+")
        self.controls_card = ctk.CTkFrame(self.workspace, fg_color=self.palette["panel"], corner_radius=18)
        self.controls_card.grid_columnconfigure(0, weight=1)
        self.controls_card.grid(row=0, column=0, padx=(0, 14), sticky="new")
        self._label(self.controls_card, "슬라이드 만들기", 0)
        self.mode_menu = self._menu(self.controls_card, 1, ["자동 모드", "수동 모드"], self.mode, self._mode_changed)
        self._label(self.controls_card, "사진 검색 키워드", 2)
        self.keyword_entry = self._entry(self.controls_card, 3, "예: 서울특별시 / 인물명 / 제품명")
        self.keyword_entry.insert(0, values.get("keyword", "")) if values.get("keyword") else None
        self._label(self.controls_card, "원본 글 제목", 4)
        self.title_entry = self._entry(self.controls_card, 5, "슬라이드 제목을 입력해 주세요")
        if values.get("title"):
            self.title_entry.insert(0, values["title"])
        self._label(self.controls_card, "원본 글 · 직접 입력 또는 기존 글 불러오기", 6)
        self.body_box = self._textbox(self.controls_card, 7, 180)
        self.body_box.insert("1.0", values.get("body", ""))
        self._button(self.controls_card, "기존 글쓰기에서 불러오기", self._import_article, 8)
        self._label(self.controls_card, "최대 슬라이드 수", 9)
        self.count_menu = self._menu(self.controls_card, 10, ["1", "3", "5", "10"], self.count, lambda _: self._save_preferences())
        self._label(self.controls_card, "색상 테마", 11)
        self.theme_menu = self._menu(self.controls_card, 12, list(THEMES), self.theme, self._edited)
        self.browser_check = ctk.CTkCheckBox(self.controls_card, text="수집 중 Chrome 보기", variable=self.show_browser,
                                           text_color=self.palette["text"], command=self._save_preferences)
        self.browser_check.grid(row=13, column=0, padx=18, pady=12, sticky="w")
        self._controls.append(self.browser_check)
        self.collect_button = self._button(self.controls_card, "위키백과·나무위키 사진 수집", lambda: self._start("collect"), 14)
        self.create_button = self._button(self.controls_card, "사진 수집 + 슬라이드 만들기", lambda: self._start("create"), 15, primary=True)
        ctk.CTkLabel(self.controls_card, text="자동: 사진 수집 후 글을 슬라이드로 구성\n수동: 글·사진을 직접 선택하고 편집\n입력한 글의 분량에 따라 실제 장수는 달라집니다.",
                     justify="left", anchor="w", text_color=self.palette["muted"], font=ctk.CTkFont(size=12)).grid(row=16, column=0, padx=18, pady=(5, 14), sticky="ew")
        self._label(self.controls_card, "저장한 작업", 17)
        self.saved_menu = self._menu(self.controls_card, 18, ["저장한 작업 없음"], command=self._select_saved)
        ctk.CTkLabel(self.controls_card, text=COPYRIGHT_NOTE, wraplength=250, justify="left", text_color=self.palette["muted"], font=ctk.CTkFont(size=12)).grid(row=19, column=0, padx=18, pady=18, sticky="ew")

        self.editor_card = ctk.CTkFrame(self.workspace, fg_color=self.palette["panel"], corner_radius=18)
        self.editor_card.grid(row=0, column=1, sticky="new")
        self.editor_card.grid_columnconfigure(0, weight=1)
        self.deck_title = self._label(self.editor_card, "슬라이드를 만들어 주세요", 0)
        self.preview = tk.Canvas(self.editor_card, height=300, background=self.palette["input"], highlightthickness=0)
        self.preview.grid(row=1, column=0, padx=18, pady=8, sticky="ew")
        self.preview.bind("<Configure>", self._resize_preview, add="+")
        nav = ctk.CTkFrame(self.editor_card, fg_color="transparent")
        nav.grid(row=2, column=0, padx=13, sticky="ew")
        nav.grid_columnconfigure((0, 2), weight=1)
        self._button(nav, "← 이전", lambda: self._move(-1), 0)
        self.page_label = ctk.CTkLabel(nav, text="0 / 0", text_color=self.palette["text"])
        self.page_label.grid(row=0, column=1, padx=12)
        self._button(nav, "다음 →", lambda: self._move(1), 0, 2)
        actions = ctk.CTkFrame(self.editor_card, fg_color="transparent")
        actions.grid(row=3, column=0, padx=13, sticky="ew")
        actions.grid_columnconfigure((0, 1, 2), weight=1)
        self._button(actions, "텍스트 복사", self._copy_text, 0)
        self._button(actions, "크게 보기", self._enlarge, 0, 1)
        self._button(actions, "이 장 삭제", self._delete_slide, 0, 2)
        self._label(self.editor_card, "이 장의 사진", 4)
        self.photo_menu = self._menu(self.editor_card, 5, ["사진 없음"], command=self._choose_photo)
        photo_actions = ctk.CTkFrame(self.editor_card, fg_color="transparent")
        photo_actions.grid(row=6, column=0, padx=13, sticky="ew")
        photo_actions.grid_columnconfigure((0, 1), weight=1)
        self._button(photo_actions, "내 이미지 넣기", self._local_photo, 0)
        self._button(photo_actions, "원본 출처 보기", self._open_source, 0, 1)
        self._label(self.editor_card, "사진 위치", 7)
        self.layout_menu = self._menu(self.editor_card, 8, ["사진 오른쪽", "사진 왼쪽"], command=self._edited)
        self.source_label = ctk.CTkLabel(self.editor_card, text="", width=1, text_color=self.palette["muted"], wraplength=300, justify="left", anchor="w", font=ctk.CTkFont(size=12))
        self.source_label.grid(row=9, column=0, padx=18, pady=10, sticky="ew")
        self._label(self.editor_card, "소제목", 10)
        self.eyebrow_entry = self._entry(self.editor_card, 11)
        self._label(self.editor_card, "제목", 12)
        self.slide_title_box = self._textbox(self.editor_card, 13, 90)
        self._label(self.editor_card, "설명", 14)
        self.summary_box = self._textbox(self.editor_card, 15, 90)
        self._label(self.editor_card, "핵심 내용 · 한 줄에 한 항목", 16)
        self.bullets_box = self._textbox(self.editor_card, 17, 120)
        ctk.CTkLabel(self.editor_card, text="편집 내용은 자동 저장됩니다. 긴 문구는 이미지에서 말줄임표로 표시됩니다.\n변경 후 PNG 저장을 눌러 최종 이미지에 반영하세요.",
                     wraplength=500, text_color=self.palette["muted"], font=ctk.CTkFont(size=12)).grid(row=18, column=0, padx=18, pady=18, sticky="ew")
        for widget in (self.eyebrow_entry, self.slide_title_box, self.summary_box, self.bullets_box):
            widget.bind("<KeyRelease>", self._edited, add="+")
        for widget in (self.keyword_entry, self.title_entry, self.body_box):
            widget.bind("<FocusOut>", lambda _: self._save_preferences(), add="+")
        footer = ctk.CTkFrame(self, fg_color=self.palette["card"], corner_radius=16)
        footer.grid(row=2, column=0, padx=28, pady=(12, 22), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(footer, text="키워드로 사진을 수집하고 원본 글을 슬라이드로 만들어 보세요.",
                                  width=1, text_color=self.palette["muted"], wraplength=400, justify="left", anchor="w", font=ctk.CTkFont(size=13))
        self.status.grid(row=0, column=0, padx=16, pady=(12, 5), sticky="ew")
        self.progress = ctk.CTkProgressBar(footer, height=5, mode="indeterminate", fg_color=self.palette["border"], progress_color=self.palette["accent"])
        self.progress.grid(row=1, column=0, padx=16, pady=(0, 6), sticky="ew")
        self.progress.set(0)
        exports = ctk.CTkFrame(footer, fg_color="transparent")
        exports.grid(row=2, column=0, padx=11, pady=(0, 8), sticky="ew")
        exports.grid_columnconfigure((0, 1, 2), weight=1)
        self._button(exports, "본문용 PNG 저장", lambda: self._start("export"), 0, primary=True)
        self._button(exports, "본문 미리보기", lambda: self._start("preview"), 0, 1)
        self._button(exports, "이미지 내보내기", self._export_folder, 0, 2)
        self._button(exports, "글쓰기 본문에 넣기", lambda: self._start("apply"), 1)
        self.cancel_button = ctk.CTkButton(exports, text="작업 취소", height=36, width=90, state="disabled", command=self.cancel.set,
                                          fg_color=self.palette["button"], text_color=self.palette["text"])
        self.cancel_button.grid(row=1, column=2, padx=5, pady=5, sticky="ew")
        self.bind("<Configure>", self._layout, add="+")
        self._mode_changed(self.mode.get())

    def _layout(self, event=None):
        if not hasattr(self, "status") or not hasattr(self, "deck_title"):
            return
        available = max(220, int(self.winfo_width() / self._get_widget_scaling()) - 90)
        editor_width = max(180, int(self.editor_card.winfo_width() / self._get_widget_scaling()) - 42)
        text_signature = (available, editor_width)
        if getattr(self, "_text_layout_signature", None) != text_signature:
            self._text_layout_signature = text_signature
            self.intro_label.configure(wraplength=available)
            if hasattr(self, "status"):
                self.status.configure(wraplength=available)
            if hasattr(self, "source_label"):
                self.source_label.configure(wraplength=editor_width)
            self.deck_title.configure(width=1, wraplength=editor_width)
        wide = self.workspace.winfo_width() / self._get_widget_scaling() >= 860
        if getattr(self, "_wide", None) == wide:
            return
        self._wide = wide
        self.workspace.grid_columnconfigure(0, weight=0 if wide else 1, minsize=300 if wide else 0)
        self.workspace.grid_columnconfigure(1, weight=1 if wide else 0)
        self.controls_card.grid_configure(row=0, column=0, padx=(0, 14) if wide else 0, pady=0)
        self.editor_card.grid_configure(row=0 if wide else 1, column=1 if wide else 0, pady=0 if wide else (16, 0))

    def _mode_changed(self, value):
        self.create_button.configure(text="사진 수집 + 슬라이드 만들기" if value == "자동 모드" else "선택한 사진으로 슬라이드 만들기")
        self._save_preferences()

    def _save_preferences(self):
        if self._closed:
            return
        self.store.save_preferences({"mode": self.mode.get(), "theme": self.theme.get(), "count": self.count.get(),
                                     "show_browser": self.show_browser.get(), "keyword": self.keyword_entry.get(),
                                     "title": self.title_entry.get(), "body": self.body_box.get("1.0", "end").strip(),
                                     "deck_id": self.deck.get("id", "") if self.deck else ""})

    def _flush_slide(self):
        if not self.deck or not self.deck.get("slides"):
            return
        slide = self.deck["slides"][self.slide_index]
        slide.update(eyebrow=self.eyebrow_entry.get().strip(), title=self.slide_title_box.get("1.0", "end").strip(),
                     summary=self.summary_box.get("1.0", "end").strip(),
                     bullets=[line.strip() for line in self.bullets_box.get("1.0", "end").splitlines() if line.strip()],
                     layout=self.layout_menu.get())
        self.deck["theme"] = self.theme.get()
        self.store.save_deck(self.deck)
        self._save_preferences()

    def _edited(self, _event=None):
        if self._edit_job:
            self.after_cancel(self._edit_job)
        self._edit_job = self.after(350, self._update_edit)

    def _update_edit(self):
        self._edit_job = None
        self._flush_slide()
        self._draw_preview()
        if self.deck:
            self.status.configure(text="편집 내용 저장 완료 · PNG 저장을 누르면 최종 이미지가 갱신됩니다.")

    def _resize_preview(self, _event=None):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._draw_preview)

    def _draw_preview(self):
        self._resize_job = None
        width = max(240, self.preview.winfo_width())
        height = round(width * 9 / 16)
        self.preview.configure(height=height)
        self.preview.delete("all")
        if not self.deck or not self.deck.get("slides"):
            self.preview.create_text(width // 2, height // 2, text="PPT 스타일 슬라이드 미리보기", fill=self.palette["muted"])
            return
        rendered = render_slide(self.deck["slides"][self.slide_index], self.theme.get(), self.slide_index + 1, len(self.deck["slides"]))
        rendered.thumbnail((width, height))
        self._preview_image = ImageTk.PhotoImage(rendered, master=self.preview)
        self.preview.create_image(width // 2, height // 2, image=self._preview_image)

    def _refresh_deck_menu(self):
        self._deck_labels = {f"{deck['created_at'][5:16].replace('T', ' ')} · {deck['title'][:22]} · {deck['count']}장 · {deck['id'][-4:]}": deck["id"]
                             for deck in self.store.list_decks()}
        self.saved_menu.configure(values=list(self._deck_labels) or ["저장한 작업 없음"])
        current_id = (self.deck or {}).get("id", "")
        selected = next((label for label, deck_id in self._deck_labels.items() if deck_id == current_id), None)
        if selected:
            self.saved_menu.set(selected)

    def _select_saved(self, label):
        if self.busy or label not in self._deck_labels:
            return
        self._flush_slide()
        try:
            self._load_deck(self.store.load_deck(self._deck_labels[label]))
        except (OSError, ValueError) as exc:
            messagebox.showerror("작업 불러오기", str(exc), parent=self.app)

    def _load_deck(self, deck):
        self.deck, self.slide_index = deck, 0
        self.keyword_entry.delete(0, "end")
        self.keyword_entry.insert(0, deck.get("keyword", ""))
        self.title_entry.delete(0, "end")
        self.title_entry.insert(0, deck.get("title", ""))
        self.body_box.delete("1.0", "end")
        self.body_box.insert("1.0", deck.get("body", ""))
        self.theme.set(deck.get("theme", "스카이"))
        self.deck_title.configure(text=deck.get("title", "새 작업"))
        self._refresh_photo_menu()
        self._refresh_deck_menu()
        self._show_slide()
        self._save_preferences()

    def _refresh_photo_menu(self):
        self._image_labels = {f"{index}. {asset.get('source', '내 이미지')} · {asset.get('title', '')[:30]}": asset
                              for index, asset in enumerate((self.deck or {}).get("images", []), 1)}
        self.photo_menu.configure(values=["사진 없음", *self._image_labels])

    def _show_slide(self):
        slides = (self.deck or {}).get("slides", [])
        if not slides:
            self.page_label.configure(text="0 / 0")
            self._draw_preview()
            return
        slide = slides[self.slide_index]
        for widget, key in ((self.eyebrow_entry, "eyebrow"), (self.slide_title_box, "title"), (self.summary_box, "summary"), (self.bullets_box, "bullets")):
            start = 0 if isinstance(widget, ctk.CTkEntry) else "1.0"
            widget.delete(start, "end")
            value = "\n".join(slide.get(key, [])) if key == "bullets" else slide.get(key, "")
            widget.insert(start, value)
        self.layout_menu.set(slide.get("layout", "사진 오른쪽"))
        asset = slide.get("image") or {}
        self.photo_menu.set(next((label for label, item in self._image_labels.items() if item.get("path") == asset.get("path")), "사진 없음"))
        self.source_label.configure(text=f"{asset.get('source', '사진 없음')} · {asset.get('title', '')}\n{asset.get('source_page', '')}".strip())
        self.page_label.configure(text=f"{self.slide_index + 1} / {len(slides)}")
        self._draw_preview()

    def _move(self, offset):
        if self.busy or not self.deck or not self.deck["slides"]:
            return
        self._flush_slide()
        self.slide_index = (self.slide_index + offset) % len(self.deck["slides"])
        self._show_slide()

    def _choose_photo(self, label):
        if self.busy or not self.deck or not self.deck["slides"]:
            return
        self._flush_slide()
        self.deck["slides"][self.slide_index]["image"] = copy.deepcopy(self._image_labels.get(label))
        self.store.save_deck(self.deck)
        self._show_slide()

    def _local_photo(self):
        if self.busy or not self.deck or not self.deck["slides"]:
            return
        selected = filedialog.askopenfilename(parent=self.app, title="슬라이드에 넣을 이미지", filetypes=[("이미지", "*.png *.jpg *.jpeg *.webp *.bmp"), ("모든 파일", "*.*")])
        if not selected:
            return
        try:
            directory = self.store.deck_directory(self.deck) / "images"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / ("manual-" + uuid.uuid4().hex[:12] + ".png")
            with Image.open(selected) as original:
                image = ImageOps.exif_transpose(original).convert("RGBA")
                image.thumbnail((2048, 2048))
                image.save(path, "PNG")
            self._flush_slide()
            asset = {"path": str(path.resolve()), "title": Path(selected).name, "source": "내 이미지"}
            self.deck["images"].append(asset)
            self.deck["slides"][self.slide_index]["image"] = asset
            self.store.save_deck(self.deck)
            self._refresh_photo_menu()
            self._show_slide()
        except (OSError, ValueError) as exc:
            messagebox.showerror("이미지 첨부", str(exc), parent=self.app)

    def _delete_slide(self):
        if self.busy or not self.deck or len(self.deck["slides"]) <= 1:
            return
        if not messagebox.askyesno("슬라이드 삭제", "현재 슬라이드를 삭제할까요? 저장한 원본 글과 사진은 유지됩니다.", parent=self.app):
            return
        self.deck["slides"].pop(self.slide_index)
        self.slide_index = min(self.slide_index, len(self.deck["slides"]) - 1)
        self.store.save_deck(self.deck)
        self._show_slide()
        self._refresh_deck_menu()

    def _copy_text(self):
        self._flush_slide()
        if not self.deck or not self.deck["slides"]:
            return
        slide = self.deck["slides"][self.slide_index]
        self.app.clipboard_clear()
        self.app.clipboard_append("\n".join([slide.get("title", ""), slide.get("summary", ""), *slide.get("bullets", [])]))
        self.status.configure(text="현재 슬라이드 텍스트를 복사했습니다.")

    def _open_source(self):
        if not self.deck or not self.deck["slides"]:
            return
        url = (self.deck["slides"][self.slide_index].get("image") or {}).get("source_page", "")
        if url.startswith(("https://ko.wikipedia.org/", "https://namu.wiki/")):
            webbrowser.open(url)

    def _import_article(self):
        if self.busy:
            return
        title = self.app.article_title_entry.get().strip()
        body = plain_text(self.app.article_editor.get("1.0", "end").strip())
        if not title and not body:
            messagebox.showinfo("원본 글 불러오기", "기존 블로그글쓰기에 작성된 글이 없습니다. 이 화면에 원본 글을 직접 입력해 주세요.", parent=self.app)
            return
        self.title_entry.delete(0, "end")
        self.title_entry.insert(0, title)
        self.body_box.delete("1.0", "end")
        self.body_box.insert("1.0", body)
        if not self.keyword_entry.get().strip():
            keyword = str(getattr(self.app, "current_keyword", "") or title)
            self.keyword_entry.insert(0, keyword)
        self._save_preferences()
        self.status.configure(text="기존 글을 복사해 가져왔습니다. 원래 글과 발행 설정은 변경하지 않았습니다.")

    @property
    def busy(self):
        # Keep the guard until the UI consumes the final event, even if the
        # worker has already finished between two rapid clicks.
        return self.worker is not None

    def _set_busy(self, busy):
        for control in self._controls:
            control.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        if busy:
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.set(0)

    def _start(self, operation, destination=None):
        if self.busy:
            return
        self._flush_slide()
        keyword = self.keyword_entry.get().strip()
        if operation in {"collect", "create"} and not keyword:
            messagebox.showinfo("키워드 입력", "사진을 검색할 키워드를 먼저 입력해 주세요.", parent=self.app)
            return
        if operation not in {"collect", "create"} and (not self.deck or not self.deck.get("slides")):
            messagebox.showinfo("슬라이드 없음", "슬라이드를 먼저 만들어 주세요.", parent=self.app)
            return
        if operation in {"collect", "create"}:
            deck = (copy.deepcopy(self.deck) if operation == "collect" and self.deck and self.deck.get("keyword") == keyword
                    else self.store.new_deck(keyword, self.title_entry.get().strip(), self.body_box.get("1.0", "end").strip(), int(self.count.get()), self.theme.get()))
            if operation == "create" and self.deck and self.deck.get("keyword") == keyword:
                deck["images"] = copy.deepcopy(self.deck.get("images", []))
        else:
            deck = copy.deepcopy(self.deck)
        automatic = self.mode.get() == "자동 모드"
        visible = self.show_browser.get()
        self.cancel = threading.Event()
        cancel, events, store, provider = self.cancel, self.events, self.store, self.chrome_path_provider
        self._set_busy(True)
        self.status.configure(text="작업을 준비합니다...")

        def run():
            warnings, paths = [], []
            try:
                if operation == "collect" or (operation == "create" and automatic):
                    deck["images"], warnings = collect_wiki_images(keyword, store.deck_directory(deck) / "images", provider(), cancel,
                                                                  lambda message: events.put(("progress", message)), visible)
                for index, slide in enumerate(deck["slides"]):
                    if not slide.get("image") and deck["images"]:
                        slide["image"] = copy.deepcopy(deck["images"][index % len(deck["images"])])
                if operation != "collect":
                    paths = export_deck(store, deck, cancel, lambda message: events.put(("progress", message)))
                else:
                    store.save_deck(deck)
                if destination:
                    target = destination / ("Tistory-V2-" + deck["id"] + "-" + uuid.uuid4().hex[:4])
                    target.mkdir(parents=True)
                    for path in [*paths, store.deck_directory(deck) / "article.html", store.deck_directory(deck) / "deck.json"]:
                        shutil.copy2(path, target / path.name)
                events.put(("done", (operation, deck, paths, warnings, destination)))
            except Cancelled:
                events.put(("cancelled", None))
            except Exception as exc:
                events.put(("error", str(exc)))

        self.worker = threading.Thread(target=run, daemon=True, name="TistoryV2Slides")
        self.worker.start()

    def _poll(self):
        self._poll_job = None
        if self._closed:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self.status.configure(text=payload)
                    continue
                self._set_busy(False)
                self.worker = None
                if kind == "done":
                    operation, deck, paths, warnings, destination = payload
                    self._load_deck(deck)
                    self._refresh_deck_menu()
                    message = f"사진 {len(deck['images'])}개 · 슬라이드 {len(deck['slides'])}장 · " + ("사진 수집 완료" if operation == "collect" else f"본문용 PNG {len(paths)}장 저장 완료")
                    if warnings:
                        message += "\n" + " / ".join(warnings)
                    self.status.configure(text=message)
                    if operation == "preview":
                        webbrowser.open((self.store.deck_directory(deck) / "article.html").resolve().as_uri())
                    elif operation == "apply":
                        self.apply_images(deck, paths)
                    elif destination:
                        self.status.configure(text=message + f"\n내보내기: {destination}")
                elif kind == "cancelled":
                    self.status.configure(text="작업을 취소했습니다. 기존 저장 작업과 원래 글은 유지됩니다.")
                elif kind == "error":
                    self.status.configure(text="작업 실패: " + payload)
                    messagebox.showerror("티스토리 V2", payload, parent=self.app)
        except queue.Empty:
            pass
        self._poll_job = self.after(150, self._poll)

    def _export_folder(self):
        if self.busy:
            return
        directory = filedialog.askdirectory(parent=self.app, title="본문용 PNG를 내보낼 폴더")
        if directory:
            self._start("export", Path(directory))

    def _enlarge(self):
        self._flush_slide()
        if not self.deck or not self.deck["slides"]:
            return
        width = min(1080, int(self.app.main_area.winfo_width() / self._get_widget_scaling()) - 40)
        height = round((width - 48) * 9 / 16) + 100
        dialog = self.app._create_in_app_completion_dialog(width, height)
        canvas = tk.Canvas(dialog, highlightthickness=0, background=self.palette["panel"])
        canvas.pack(fill="both", expand=True, padx=18, pady=(18, 5))
        rendered = render_slide(self.deck["slides"][self.slide_index], self.theme.get(), self.slide_index + 1, len(self.deck["slides"]))
        rendered.thumbnail((round((width - 48) * self._get_widget_scaling()), round((height - 90) * self._get_widget_scaling())))
        canvas._v2_photo = ImageTk.PhotoImage(rendered, master=canvas)
        canvas.create_image(0, 0, anchor="nw", image=canvas._v2_photo)

        def close(_event=None):
            dialog.grab_release()
            dialog.destroy()

        ctk.CTkButton(dialog, text="닫기", command=close, width=120, height=34).pack(pady=(5, 14))
        dialog.grab_set()
        dialog.focus_set()
        tk.Misc.bind(dialog, "<Escape>", close, add="+")

    def destroy(self):
        if not self._closed:
            self._flush_slide()
            self._save_preferences()
            self._closed = True
            self.cancel.set()
            for job in (self._poll_job, self._edit_job, self._resize_job):
                if job:
                    self.after_cancel(job)
        super().destroy()
