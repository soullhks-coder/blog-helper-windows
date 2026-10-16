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
from tistory_v2 import (COPYRIGHT_NOTE, THEME_CHOICES, TEXT_HORIZONTAL_POSITIONS, TEXT_VERTICAL_POSITIONS,
                        Cancelled, V2Store, collect_wiki_images,
                        SLIDE_SIZE, apply_text_positions, assign_deck_themes, assign_slide_images, build_slides, deck_slide_size,
                        deck_slide_sizes, deck_wiki_keyword, export_deck, plain_text, render_slide, slide_output_size,
                        text_position_defaults, validate_slide_size, SLIDE_FONT_SIZE_RANGE, SLIDE_FONT_SIZES,
                        apply_slide_font_sizes, slide_font_sizes, slide_font_size_overrides, validate_slide_font_size)
from tistory_v2_summary import build_summary_slides

filedialog = DimmedDialogApi(filedialog)
messagebox = DimmedDialogApi(messagebox)


class TistoryV2Panel(ctk.CTkFrame):
    def __init__(self, master, app, directory: Path, chrome_path_provider, apply_images, workflow=None,
                 article_provider=None, on_finished=None):
        super().__init__(master, fg_color="transparent")
        self.article_provider = article_provider
        self.embedded = article_provider is not None
        self.on_finished = on_finished
        self._uses_writing_design_theme = True
        # Map the lazy page before CTk dropdowns run their idle geometry work.
        self.grid(row=1 if self.embedded else 0, column=0, sticky="nsew")
        self.app = app
        self.palette = app._theme_palette()
        self.store = V2Store(directory)
        self.chrome_path_provider = chrome_path_provider
        self.apply_images = apply_images
        self.workflow = workflow
        self._connection_values = {}
        self._connection_preferences = {}
        self._browser_open = False
        self._publish_locked = False
        self.deck = None
        self.slide_index = 0
        self.worker = None
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self._poll_job = self._edit_job = self._resize_job = None
        self._font_preview_job = None
        self._syncing_font_sizes = False
        self._closed = False
        self._controls = []
        self._preview_image = None
        self._deck_labels = {}
        self._image_labels = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        values = self.store.preferences()
        self._text_position_defaults = self.store.slide_position_defaults()
        self._font_size_defaults = slide_font_size_overrides(values.get("font_size_defaults"))
        try:
            self._output_size = deck_slide_size(values)
        except ValueError:
            self._output_size = SLIDE_SIZE
        self.mode = tk.StringVar(value=values.get("mode") if values.get("mode") in {"자동 모드", "수동 모드"} else "자동 모드")
        self.theme = tk.StringVar(value=values.get("theme") if values.get("theme") in THEME_CHOICES else "스카이")
        self.count = tk.StringVar(value="10")
        self.show_browser = tk.BooleanVar(value=bool(values.get("show_browser", True)))
        self.show_numbers = tk.BooleanVar(value=bool(values.get("show_numbers", True)))
        self.show_photo_sources = tk.BooleanVar(value=bool(values.get("show_photo_sources", True)))
        self._build(values)
        self.refresh_connections(values)
        self._refresh_deck_menu()
        saved_id = values.get("deck_id", "")
        if saved_id:
            try:
                self._load_deck(self.store.load_deck(saved_id))
                self.refresh_connections(values)
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

    def _font_size_control(self, parent, label, row, column, key):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.grid(row=row, column=column, padx=8, pady=5, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=label, anchor="w", text_color=self.palette["text"],
                     font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w")
        slider = ctk.CTkSlider(
            header, from_=SLIDE_FONT_SIZE_RANGE[0], to=SLIDE_FONT_SIZE_RANGE[1], number_of_steps=90,
            width=76, height=16, command=lambda value, field=key: self.font_size_vars[field].set(str(round(value))),
        )
        slider.grid(row=0, column=1, padx=(4, 7), sticky="e")
        variable = tk.StringVar(value=str(self._font_size_defaults.get(key, SLIDE_FONT_SIZES[key])))
        entry = ctk.CTkEntry(header, width=44, height=28, textvariable=variable, justify="center",
                             fg_color=self.palette["input"], border_color=self.palette["border"],
                             text_color=self.palette["text"], font=ctk.CTkFont(size=12))
        entry.grid(row=0, column=2, sticky="e")
        ctk.CTkLabel(header, text="px", width=18, text_color=self.palette["muted"],
                     font=ctk.CTkFont(size=11)).grid(row=0, column=3, padx=(3, 0))
        self.font_size_vars[key], self.font_size_sliders[key], self.font_size_entries[key] = variable, slider, entry
        slider.set(int(variable.get()))
        variable.trace_add("write", lambda *_args, field=key: self._font_size_changed(field))
        entry.bind("<Return>", lambda _event, field=key: self._finish_font_size_input(field), add="+")
        entry.bind("<FocusOut>", lambda _event, field=key: self._finish_font_size_input(field), add="+")
        self._controls.extend((slider, entry))

    def _build(self, values):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=28, pady=(26, 14), sticky="ew")
        ctk.CTkLabel(header, text="본문 슬라이드 편집" if self.embedded else "티스토리 V2", font=ctk.CTkFont(size=20 if self.embedded else 30, weight="bold"), text_color=self.palette["text"]).pack(anchor="w")
        self.intro_label = ctk.CTkLabel(header, text=("③에서 확인한 글로 600×300 슬라이드 10장 · 문구와 사진을 수정한 그대로 2장씩 콜라주합니다." if self.embedded else "프롬프트 글쓰기 → 소제목별 600×300 슬라이드 10장 → 티스토리 콜라주 입력"),
                                      width=1, wraplength=400, anchor="w", justify="left",
                                      text_color=self.palette["muted"], font=ctk.CTkFont(size=13))
        self.intro_label.pack(fill="x", pady=(5, 0))
        # Stage 4 already has the application's common scroll container.
        self.scroll = (ctk.CTkFrame(self, fg_color="transparent") if self.embedded else
                       ctk.CTkScrollableFrame(self, fg_color="transparent", scrollbar_button_color=self.palette["divider"]))
        self.scroll.grid(row=1, column=0, padx=28, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)
        self.connection_card = ctk.CTkFrame(self.scroll, fg_color=self.palette["card"], corner_radius=18)
        self.connection_card.grid(row=0, column=0, pady=(0, 14), sticky="ew")
        self.connection_card.grid_columnconfigure((0, 1, 2), weight=1)
        self.connection_menus = {}
        for column, (key, text) in enumerate((("profile", "티스토리 블로그"), ("prompt_id", "글쓰기 프롬프트"), ("thumbnail", "썸네일 관리"))):
            group = ctk.CTkFrame(self.connection_card, fg_color="transparent")
            group.grid(row=0, column=column, padx=5, pady=(0, 6), sticky="ew")
            group.grid_columnconfigure(0, weight=1)
            self._label(group, text, 0)
            menu = self._menu(group, 1, ["불러오는 중"], command=lambda _, k=key: self._connection_changed(k))
            self.connection_menus[key] = menu
        model_group = ctk.CTkFrame(self.connection_card, fg_color="transparent")
        model_group.grid(row=1, column=0, padx=5, pady=(0, 14), sticky="ew")
        model_group.grid_columnconfigure(0, weight=1)
        self._label(model_group, "글쓰기 모델", 0)
        self.model_menu = self._menu(model_group, 1, ["CLI", "GPT API", "제미나이 API"], command=lambda _: self._save_preferences())
        actions = ctk.CTkFrame(self.connection_card, fg_color="transparent")
        actions.grid(row=1, column=1, columnspan=2, padx=18, pady=(18, 8), sticky="sew")
        actions.grid_columnconfigure((0, 1), weight=1)
        self._button(actions, "연결 목록 새로고침", self.refresh_connections, 0)
        self._button(actions, "썸네일 관리 열기", lambda: self.app._switch_page("thumbnails"), 0, 1)
        if self.embedded:
            self.connection_card.grid_remove()
        self.workspace = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.workspace.grid(row=1, column=0, sticky="ew")
        self.workspace.bind("<Configure>", self._layout, add="+")
        self.controls_card = ctk.CTkFrame(self.workspace, fg_color=self.palette["panel"], corner_radius=18)
        self.controls_card.grid_columnconfigure(0, weight=1)
        self.controls_card.grid(row=0, column=0, padx=(0, 14), sticky="new")
        self._label(self.controls_card, "슬라이드 만들기", 0)
        self.mode_menu = self._menu(self.controls_card, 1, ["자동 모드", "수동 모드"], self.mode, self._mode_changed)
        self._label(self.controls_card, "글쓰기 · 사진 검색 키워드", 2)
        self.keyword_entry = self._entry(self.controls_card, 3, "예: 서울특별시 / 인물명 / 제품명")
        self.keyword_entry.insert(0, values.get("keyword", "")) if values.get("keyword") else None
        self._label(self.controls_card, "원본 글 제목", 4)
        self.title_entry = self._entry(self.controls_card, 5, "슬라이드 제목을 입력해 주세요")
        if values.get("title"):
            self.title_entry.insert(0, values["title"])
        self._label(self.controls_card, "본문 · 자동 모드에서는 참고내용으로 사용", 6)
        self.body_box = self._textbox(self.controls_card, 7, 180)
        self.body_box.insert("1.0", values.get("body", ""))
        self._button(self.controls_card, "기존 글쓰기에서 불러오기", self._import_article, 8)
        self.size_label = self._label(self.controls_card, "본문 슬라이드 10장 · 600×300 PNG", 9)
        ctk.CTkLabel(self.controls_card, text="1·2번: 위키 사진 포함\n3~10번: 공통 스타일 · 2장씩 콜라주 5개", anchor="w", justify="left", text_color=self.palette["muted"]).grid(row=10, column=0, padx=18, sticky="ew")
        self._label(self.controls_card, "색상 테마", 11)
        self.theme_menu = self._menu(self.controls_card, 12, list(THEME_CHOICES), self.theme, self._theme_changed)
        display_options = ctk.CTkFrame(self.controls_card, fg_color="transparent")
        display_options.grid(row=13, column=0, padx=18, pady=12, sticky="ew")
        display_options.grid_columnconfigure(0, weight=1)
        size_options = ctk.CTkFrame(display_options, fg_color="transparent")
        size_options.grid(row=0, column=0, pady=(0, 16), sticky="ew")
        size_options.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(size_options, text="이미지 크기 · 10장 공통", anchor="w", text_color=self.palette["text"],
                     font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        self.size_entries = []
        for column, (label, value) in enumerate(zip(("가로 px", "세로 px"), self._output_size)):
            group = ctk.CTkFrame(size_options, fg_color="transparent")
            group.grid(row=1, column=column, padx=(0, 6), sticky="ew")
            group.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(group, text=label, anchor="w", text_color=self.palette["muted"]).grid(row=0, column=0, sticky="ew")
            entry = ctk.CTkEntry(group, width=70, height=34, fg_color=self.palette["input"],
                                  border_color=self.palette["border"], text_color=self.palette["text"])
            entry.grid(row=1, column=0, sticky="ew")
            entry.insert(0, str(value))
            entry.bind("<Return>", lambda _: self._apply_image_size(), add="+")
            self.size_entries.append(entry)
            self._controls.append(entry)
        self.size_apply_button = self._button(size_options, "전체 적용", self._apply_image_size, 1, 2, primary=True)
        self.size_apply_button.configure(width=80, height=34)
        self.size_apply_button.grid_configure(sticky="sew", padx=0, pady=0)
        self.size_reset_button = self._button(size_options, "600×300 기본값", self._reset_image_size, 2)
        self.size_reset_button.grid_configure(columnspan=3, padx=0)
        ctk.CTkLabel(size_options, text="가로 300~2400 · 세로 150~2400px", anchor="w",
                     text_color=self.palette["muted"], font=ctk.CTkFont(size=11)).grid(row=3, column=0, columnspan=3, sticky="ew")
        self.number_check = ctk.CTkCheckBox(display_options, text="슬라이드 번호 표시", variable=self.show_numbers,
                                          text_color=self.palette["text"], command=self._number_display_changed)
        self.number_check.grid(row=1, column=0, pady=(0, 12), sticky="w")
        self._controls.append(self.number_check)
        self.photo_sources_check = ctk.CTkCheckBox(
            display_options, text="사진출처 표시", variable=self.show_photo_sources,
            text_color=self.palette["text"], command=self._photo_sources_changed)
        self.photo_sources_check.grid(row=2, column=0, pady=(0, 12), sticky="w")
        self._controls.append(self.photo_sources_check)
        self.browser_check = ctk.CTkCheckBox(display_options, text="수집 중 Chrome 보기", variable=self.show_browser,
                                           text_color=self.palette["text"], command=self._save_preferences)
        self.browser_check.grid(row=3, column=0, sticky="w")
        self._controls.append(self.browser_check)
        self.collect_button = self._button(self.controls_card, "위키백과·나무위키 사진 수집", lambda: self._start("collect"), 14)
        self.create_button = self._button(self.controls_card, "사진 수집 + 슬라이드 만들기", lambda: self._start("create"), 15, primary=True)
        ctk.CTkLabel(self.controls_card, text="자동: 선택한 프롬프트로 글을 쓴 후 사진·슬라이드 제작\n수동: 입력한 글의 소제목 순서대로 10장 제작", wraplength=260,
                     justify="left", anchor="w", text_color=self.palette["muted"], font=ctk.CTkFont(size=12)).grid(row=16, column=0, padx=18, pady=(5, 14), sticky="ew")
        self._label(self.controls_card, "저장한 작업", 17)
        self.saved_menu = self._menu(self.controls_card, 18, ["저장한 작업 없음"], command=self._select_saved)
        ctk.CTkLabel(self.controls_card, text=COPYRIGHT_NOTE, wraplength=250, justify="left", text_color=self.palette["muted"], font=ctk.CTkFont(size=12)).grid(row=19, column=0, padx=18, pady=18, sticky="ew")
        if self.embedded:
            # Keep the source widgets for saved-deck compatibility, but the
            # single source of article text is now the shared stage-3 editor.
            for widget in self.controls_card.grid_slaves():
                if int(widget.grid_info()["row"]) in {2, 3, 4, 5, 6, 7, 8, 16}:
                    widget.grid_remove()
            self.article_source_label = ctk.CTkLabel(
                self.controls_card, text="③ 글 확인·수정에서 완성한 본문을 사용합니다.",
                width=1, wraplength=250, justify="left", anchor="w", text_color=self.palette["muted"])
            self.article_source_label.grid(row=2, column=0, padx=18, pady=14, sticky="ew")
            ctk.CTkLabel(self.controls_card, text="V2 글 작성 시 슬라이드 요약 지침이 자동 추가됩니다.\n자동: 별도 요약 문구 + 위키 사진 → 10장\n수동: 별도 요약 문구 → 10장 후 사진 직접 선택",
                         wraplength=250, justify="left", text_color=self.palette["muted"],
                         font=ctk.CTkFont(size=12)).grid(row=16, column=0, padx=18, pady=12, sticky="ew")

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
        self._button(actions, "이 장 초기화", self._reset_slide, 0, 2)
        self.font_size_panel = ctk.CTkFrame(self.editor_card, fg_color=self.palette["input"],
                                           border_width=1, border_color=self.palette["border"], corner_radius=12)
        self.font_size_panel.grid(row=4, column=0, padx=18, pady=(8, 0), sticky="ew")
        self.font_size_panel.grid_columnconfigure((0, 1), weight=1, uniform="font-sizes")
        self.font_size_vars, self.font_size_sliders, self.font_size_entries = {}, {}, {}
        for index, (key, label) in enumerate((("eyebrow", "소제목"), ("title", "제목"),
                                              ("summary", "설명"), ("bullets", "핵심 내용"))):
            self._font_size_control(self.font_size_panel, label, index // 2, index % 2, key)
        font_actions = ctk.CTkFrame(self.font_size_panel, fg_color="transparent")
        font_actions.grid(row=2, column=0, columnspan=2, padx=3, pady=(0, 3), sticky="ew")
        font_actions.grid_columnconfigure((0, 1), weight=1)
        self.font_size_all_button = self._button(font_actions, "글자 크기 전체 적용", self._apply_font_sizes_to_all, 0, primary=True)
        self.font_size_reset_button = self._button(font_actions, "이 장 크기 초기화", self._reset_font_sizes, 0, 1)
        positions = ctk.CTkFrame(self.editor_card, fg_color="transparent")
        positions.grid(row=5, column=0, padx=5, pady=(0, 10), sticky="ew")
        positions.grid_columnconfigure((0, 1), weight=1, uniform="text-position")
        self.text_position_menus = {}
        for column, (key, label, values) in enumerate((
                ("text_horizontal", "문구 가로 위치", TEXT_HORIZONTAL_POSITIONS),
                ("text_vertical", "문구 세로 위치", TEXT_VERTICAL_POSITIONS))):
            group = ctk.CTkFrame(positions, fg_color="transparent")
            group.grid(row=0, column=column, sticky="ew")
            group.grid_columnconfigure(0, weight=1)
            self._label(group, label, 0)
            self.text_position_menus[key] = self._menu(group, 1, list(values), command=self._text_position_changed)
            self.text_position_menus[key].set(self._text_position_defaults[key])
        self.text_position_all_button = self._button(
            positions, "현재 위치를 전체 슬라이드에 적용", self._apply_text_position_to_all, 1, primary=True)
        self.text_position_all_button.grid_configure(columnspan=2, padx=18, pady=(10, 0))
        ctk.CTkLabel(positions, text="선택한 위치는 다음 새 슬라이드에도 자동 적용됩니다.", width=1,
                     wraplength=300, text_color=self.palette["muted"], font=ctk.CTkFont(size=11)).grid(
                         row=2, column=0, columnspan=2, padx=18, pady=(6, 0), sticky="ew")
        self._label(positions, "이 장 이미지 크기", 3).grid_configure(columnspan=2)
        single_size = ctk.CTkFrame(positions, fg_color="transparent")
        single_size.grid(row=4, column=0, columnspan=2, padx=18, sticky="ew")
        single_size.grid_columnconfigure((0, 1), weight=1)
        self.slide_size_entries = []
        for column, label in enumerate(("가로 px", "세로 px")):
            group = ctk.CTkFrame(single_size, fg_color="transparent")
            group.grid(row=0, column=column, padx=(0, 6), sticky="ew")
            group.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(group, text=label, anchor="w", text_color=self.palette["muted"]).grid(row=0, column=0, sticky="ew")
            entry = ctk.CTkEntry(group, width=70, height=34, fg_color=self.palette["input"],
                                  border_color=self.palette["border"], text_color=self.palette["text"])
            entry.grid(row=1, column=0, sticky="ew")
            entry.insert(0, str(self._output_size[column]))
            entry.bind("<Return>", lambda _: self._apply_slide_image_size(), add="+")
            self.slide_size_entries.append(entry)
            self._controls.append(entry)
        self.slide_size_apply_button = self._button(single_size, "이 장 적용", self._apply_slide_image_size, 0, 2, primary=True)
        self.slide_size_apply_button.configure(width=80, height=34)
        self.slide_size_apply_button.grid_configure(sticky="sew", padx=0, pady=0)
        self.slide_size_common_button = self._button(single_size, "이 장만 공통 크기로", self._use_common_slide_size, 1)
        self.slide_size_common_button.grid_configure(columnspan=3, padx=0)
        self._label(self.editor_card, "이 장의 사진", 6)
        self.photo_menu = self._menu(self.editor_card, 7, ["사진 없음"], command=self._choose_photo)
        photo_actions = ctk.CTkFrame(self.editor_card, fg_color="transparent")
        photo_actions.grid(row=8, column=0, padx=13, sticky="ew")
        photo_actions.grid_columnconfigure((0, 1), weight=1)
        self._button(photo_actions, "내 이미지 넣기", self._local_photo, 0)
        self._button(photo_actions, "원본 출처 보기", self._open_source, 0, 1)
        self._label(self.editor_card, "사진 위치", 9)
        self.layout_menu = self._menu(self.editor_card, 10, ["사진 오른쪽", "사진 왼쪽", "공통 스타일"], command=self._edited)
        self.source_label = ctk.CTkLabel(self.editor_card, text="", width=1, text_color=self.palette["muted"], wraplength=300, justify="left", anchor="w", font=ctk.CTkFont(size=12))
        self.source_label.grid(row=11, column=0, padx=18, pady=10, sticky="ew")
        self._label(self.editor_card, "소제목", 12)
        self.eyebrow_entry = self._entry(self.editor_card, 13)
        self._label(self.editor_card, "제목", 14)
        self.slide_title_box = self._textbox(self.editor_card, 15, 90)
        self._label(self.editor_card, "설명", 16)
        self.summary_box = self._textbox(self.editor_card, 17, 90)
        self._label(self.editor_card, "핵심 내용 · 한 줄에 한 항목", 18)
        self.bullets_box = self._textbox(self.editor_card, 19, 120)
        ctk.CTkLabel(self.editor_card, text="글자 크기 6~96px · 600×300 기준이며 이미지 크기에 비례합니다.\n크기 변경은 미리보기에 바로 반영되고 자동 저장됩니다. 핵심 내용은 한 줄에 한 항목씩 입력하세요.\n큰 글씨·긴 문구는 일부가 생략될 수 있습니다. PNG 저장 또는 업로드 시 같은 설정을 사용합니다.",
                     width=1, wraplength=360, justify="left", text_color=self.palette["muted"], font=ctk.CTkFont(size=12)).grid(row=20, column=0, padx=18, pady=18, sticky="ew")
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
        self.apply_button = self._button(exports, "글쓰기 본문에 넣기", lambda: self._start("apply"), 1)
        self.editor_button = self._button(exports, "티스토리 입력 · 콜라주", lambda: self._start("editor"), 1, 1, primary=True)
        if self.embedded:
            self.apply_button.grid_remove()
            self.editor_button.grid_remove()
            ctk.CTkLabel(exports, text="아래 업로드 버튼은 현재 편집한 10장을 사용합니다.",
                         width=1, wraplength=350, text_color=self.palette["muted"]).grid(row=1, column=0, columnspan=2, sticky="ew", padx=5)
        self.cancel_button = ctk.CTkButton(exports, text="작업 취소", height=36, width=90, state="disabled", command=self._cancel_work,
                                          fg_color=self.palette["button"], text_color=self.palette["text"])
        self.cancel_button.grid(row=1, column=2, padx=5, pady=5, sticky="ew")
        self.bind("<Configure>", self._layout, add="+")
        self._update_size_labels()
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
        if self.embedded:
            self.create_button.configure(text="현재 본문 + 사진으로 10장 만들기" if value == "자동 모드" else "현재 본문으로 10장 만들기")
        else:
            self.create_button.configure(text="프롬프트 글쓰기 + 10장 만들기" if value == "자동 모드" else "입력한 글로 10장 만들기")
        self._save_preferences()

    def refresh_connections(self, preferences=None):
        if self.busy or not self.workflow:
            return
        previous = preferences if isinstance(preferences, dict) else self._selection()
        options = self.workflow.options()
        self._connection_values = {
            "profile": {p["label"]: p["id"] for p in options["profiles"]},
            "prompt_id": {p["label"]: p["id"] for p in options["prompts"]},
            "thumbnail": {label: index for index, label in enumerate(options["thumbnails"])},
        }
        saved_preferences = previous.get("blog_preferences", self._connection_preferences)
        self._connection_preferences = copy.deepcopy(saved_preferences) if isinstance(saved_preferences, dict) else {}
        defaults = {"profile": options["active_profile"], "prompt_id": "", "thumbnail": options["thumbnail"]}
        for key, menu in self.connection_menus.items():
            choices = self._connection_values[key]
            menu.configure(values=list(choices) or ["블로그 미등록" if key == "profile" else "선택 항목 없음"])
            wanted = previous.get(key, defaults[key])
            menu.set(next((label for label, identity in choices.items() if identity == wanted), next(iter(choices), "선택 항목 없음")))
        self.model_menu.configure(values=options["models"])
        self.model_menu.set(previous.get("model") if previous.get("model") in options["models"] else options["model"])
        self._save_preferences()

    def _selection(self):
        if self.embedded:
            return self.app.tistory_v2_controls.selection()
        selected = {key: self._connection_values.get(key, {}).get(menu.get()) for key, menu in self.connection_menus.items()}
        selected.update(model=self.model_menu.get(), blog_preferences=copy.deepcopy(self._connection_preferences))
        return selected

    def _connection_changed(self, key):
        selection = self._selection()
        if key == "profile":
            saved = self._connection_preferences.get(selection["profile"], {})
            for field in ("prompt_id", "thumbnail"):
                choices = self._connection_values[field]
                label = next((label for label, identity in choices.items() if identity == saved.get(field)), None)
                if label:
                    self.connection_menus[field].set(label)
        self._save_preferences()

    def _save_preferences(self):
        if self._closed:
            return
        # Embedded top selectors save their own values; they may already have
        # been destroyed when Tk recursively closes this stage-4 panel.
        selection = {} if self.embedded else self._selection()
        if selection.get("profile"):
            self._connection_preferences[selection["profile"]] = {key: selection.get(key) for key in ("prompt_id", "thumbnail")}
        latest = self.store.preferences()
        connection = {} if self.embedded else {**selection, "blog_preferences": copy.deepcopy(self._connection_preferences)}
        self.store.save_preferences({**latest, **connection, "mode": self.mode.get(), "theme": self.theme.get(), "count": "10",
                                     "slide_size": list(self._output_size),
                                     "text_position_defaults": dict(self._text_position_defaults),
                                     "font_size_defaults": dict(self._font_size_defaults),
                                     "show_browser": self.show_browser.get(), "show_numbers": self.show_numbers.get(),
                                     "show_photo_sources": self.show_photo_sources.get(),
                                     "keyword": self.keyword_entry.get(),
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
        slide.update({key: menu.get() for key, menu in self.text_position_menus.items()})
        assign_deck_themes(self.deck, self.theme.get())
        self.deck["show_numbers"] = self.show_numbers.get()
        self.deck["show_photo_sources"] = self.show_photo_sources.get()
        self.deck["slide_size"] = list(self._output_size)
        self.deck["font_size_defaults"] = dict(self._font_size_defaults)
        if not self.embedded:
            self.deck["title"] = self.title_entry.get().strip() or self.deck.get("title", "")
            self.deck["body"] = self.body_box.get("1.0", "end").strip()
        self.store.save_deck(self.deck)
        self._save_preferences()

    def _commit_image_size(self, apply_all=False):
        size = validate_slide_size([entry.get() for entry in self.size_entries])
        reset_overrides = apply_all or size != self._output_size
        self._output_size = size
        if self.deck:
            self.deck["slide_size"] = list(size)
            if reset_overrides:
                for slide in self.deck.get("slides", []):
                    slide.pop("slide_size", None)
        if reset_overrides:
            self._sync_slide_size_entries()
        self._update_size_labels()
        self._flush_slide()
        self._save_preferences()

    def _update_size_labels(self):
        width, height = self._output_size
        individual = sum("slide_size" in slide for slide in (self.deck or {}).get("slides", []))
        suffix = f" · 개별 {individual}장" if individual else ""
        self.size_label.configure(text=f"슬라이드 10장 · 기본 {width}×{height}{suffix}")
        self.intro_label.configure(text=(f"③에서 확인한 글로 슬라이드 10장 · 기본 {width}×{height}, 장별 크기 변경 가능 · 2장씩 콜라주합니다."
                                        if self.embedded else f"프롬프트 글쓰기 → 슬라이드 10장(기본 {width}×{height}, 개별 크기 지원) → 티스토리 콜라주 입력"))

    def _current_slide_size(self):
        if not self.deck or not self.deck.get("slides"):
            return self._output_size
        return slide_output_size(self.deck["slides"][self.slide_index], self._output_size)

    def _sync_slide_size_entries(self):
        for entry, value in zip(self.slide_size_entries, self._current_slide_size()):
            entry.delete(0, "end")
            entry.insert(0, str(value))

    def _commit_slide_image_size(self):
        if not self.deck or not self.deck.get("slides"):
            return
        size = validate_slide_size([entry.get() for entry in self.slide_size_entries])
        slide = self.deck["slides"][self.slide_index]
        if size == self._output_size:
            slide.pop("slide_size", None)
        else:
            slide["slide_size"] = list(size)
        self._flush_slide()
        self._update_size_labels()

    def _apply_slide_image_size(self):
        if self.busy or self._publishing():
            return False
        if not self.deck or not self.deck.get("slides"):
            messagebox.showinfo("슬라이드 없음", "슬라이드를 먼저 만들어 주세요.", parent=self.app)
            return False
        try:
            self._commit_slide_image_size()
        except ValueError as exc:
            messagebox.showerror("이 장 이미지 크기", str(exc), parent=self.app)
            return False
        self._draw_preview()
        width, height = self._current_slide_size()
        self.status.configure(text=f"{self.slide_index + 1}번 슬라이드 {width}×{height}px 저장 완료 · 다른 장의 크기는 유지됩니다.")
        return True

    def _use_common_slide_size(self):
        if self.busy or self._publishing():
            return
        for entry, value in zip(self.slide_size_entries, self._output_size):
            entry.delete(0, "end")
            entry.insert(0, str(value))
        self._apply_slide_image_size()

    def _apply_image_size(self):
        if self.busy or self._publishing():
            return False
        try:
            self._commit_image_size(apply_all=True)
        except ValueError as exc:
            messagebox.showerror("이미지 크기", str(exc), parent=self.app)
            return False
        self._draw_preview()
        self.status.configure(text=f"{self._output_size[0]}×{self._output_size[1]}px 저장 완료 · PNG 저장 또는 업로드 시 10장 모두 반영됩니다.")
        return True

    def _reset_image_size(self):
        if self.busy or self._publishing():
            return
        for entry, value in zip(self.size_entries, SLIDE_SIZE):
            entry.delete(0, "end")
            entry.insert(0, str(value))
        self._apply_image_size()

    def _number_display_changed(self):
        self._flush_slide()
        self._save_preferences()
        self._draw_preview()
        self.status.configure(text="번호 표시 설정 저장 완료 · PNG 저장 또는 업로드 시 모든 슬라이드에 반영됩니다.")

    def _photo_sources_changed(self):
        if self.busy or self._publishing():
            return
        self._flush_slide()
        self._save_preferences()
        self._draw_preview()
        self.status.configure(text="사진출처 표시 설정 저장 완료 · 미리보기와 PNG 저장·본문·업로드에 동일하게 적용됩니다.")

    def _theme_changed(self, _value=None):
        if self.busy or self._publishing():
            return
        if self._edit_job:
            self.after_cancel(self._edit_job)
            self._edit_job = None
        if self.deck:
            assign_deck_themes(self.deck, self.theme.get(), reshuffle=self.theme.get() == "랜덤")
        self._flush_slide()
        self._save_preferences()
        if self.deck:
            self._show_slide()
        else:
            self._draw_preview()
        self.status.configure(text=("장별 랜덤 색상 저장 완료 · 미리보기·PNG·업로드에 같은 색상이 적용됩니다."
                                    if self.theme.get() == "랜덤" else "색상 테마 저장 완료 · PNG 저장 또는 업로드 시 반영됩니다."))

    def _text_position_changed(self, _value=None):
        if self.busy or self._publishing():
            return
        if self._edit_job:
            self.after_cancel(self._edit_job)
        self._text_position_defaults = text_position_defaults({
            "text_position_defaults": {key: menu.get() for key, menu in self.text_position_menus.items()}})
        self._update_edit()
        self._save_preferences()

    def _apply_text_position_to_all(self):
        if self.busy or self._publishing():
            return False
        if not self.deck or not self.deck.get("slides"):
            messagebox.showinfo("슬라이드 없음", "슬라이드를 먼저 만들어 주세요.", parent=self.app)
            return False
        if self._edit_job:
            self.after_cancel(self._edit_job)
            self._edit_job = None
        # Flush a still-pending text edit before bulk-changing positions only.
        self._text_position_defaults = text_position_defaults({
            "text_position_defaults": {key: menu.get() for key, menu in self.text_position_menus.items()}})
        self._flush_slide()
        horizontal = self.text_position_menus["text_horizontal"].get()
        vertical = self.text_position_menus["text_vertical"].get()
        apply_text_positions(self.deck["slides"], horizontal, vertical)
        self.store.save_deck(self.deck)
        self._save_preferences()
        self._draw_preview()
        self.status.configure(text=f"전체 {len(self.deck['slides'])}장 문구 위치 저장 완료 · {horizontal} / {vertical}\nPNG 저장 또는 업로드 시 모두 반영됩니다. 각 장은 다시 개별 수정할 수 있습니다.")
        return True

    def _edited(self, _event=None):
        if self._edit_job:
            self.after_cancel(self._edit_job)
        self._edit_job = self.after(350, self._update_edit)

    def _sync_slide_font_sizes(self):
        slide = self.deck["slides"][self.slide_index] if self.deck and self.deck.get("slides") else {"font_sizes": self._font_size_defaults}
        self._syncing_font_sizes = True
        try:
            for key, value in slide_font_sizes(slide, self.slide_index + 1).items():
                self.font_size_vars[key].set(str(value))
                self.font_size_sliders[key].set(value)
                self.font_size_entries[key].configure(border_color=self.palette["border"])
        finally:
            self._syncing_font_sizes = False

    def _font_size_changed(self, key):
        if self._syncing_font_sizes or self.busy or self._publishing():
            return
        try:
            value = validate_slide_font_size(self.font_size_vars[key].get())
        except ValueError:
            self.font_size_entries[key].configure(border_color="#ef4444")
            return
        self.font_size_entries[key].configure(border_color=self.palette["border"])
        self.font_size_sliders[key].set(value)
        self._font_size_defaults[key] = value
        if self.deck and self.deck.get("slides"):
            slide = self.deck["slides"][self.slide_index]
            slide["font_sizes"] = {**slide_font_size_overrides(slide.get("font_sizes")), key: value}
        self._edited()
        if self._font_preview_job:
            self.after_cancel(self._font_preview_job)
        self._font_preview_job = self.after(40, self._preview_font_size)

    def _preview_font_size(self):
        self._font_preview_job = None
        self._draw_preview()

    def _finish_font_size_input(self, key):
        if self._closed or self._syncing_font_sizes or self.busy or self._publishing():
            return
        try:
            validate_slide_font_size(self.font_size_vars[key].get())
        except ValueError:
            self._sync_slide_font_sizes()
            self.status.configure(text="글자 크기는 6~96 사이의 정수로 입력해 주세요. 이전 크기를 유지했습니다.")

    def _apply_font_sizes_to_all(self):
        if self.busy or self._publishing():
            return False
        if not self.deck or not self.deck.get("slides"):
            messagebox.showinfo("슬라이드 없음", "슬라이드를 먼저 만들어 주세요.", parent=self.app)
            return False
        try:
            sizes = {key: validate_slide_font_size(value.get()) for key, value in self.font_size_vars.items()}
        except ValueError as exc:
            self.status.configure(text=str(exc))
            return False
        if self._edit_job:
            self.after_cancel(self._edit_job)
            self._edit_job = None
        self._flush_slide()
        apply_slide_font_sizes(self.deck["slides"], sizes)
        self._font_size_defaults = dict(sizes)
        self.deck["font_size_defaults"] = dict(sizes)
        self.store.save_deck(self.deck)
        self._save_preferences()
        self._draw_preview()
        self.status.configure(text=f"전체 {len(self.deck['slides'])}장 글자 크기 저장 완료 · 각 장은 다시 개별 수정할 수 있습니다.")
        return True

    def _reset_font_sizes(self):
        if self.busy or self._publishing():
            return
        if self._edit_job:
            self.after_cancel(self._edit_job)
            self._edit_job = None
        if self.deck and self.deck.get("slides"):
            self.deck["slides"][self.slide_index].pop("font_sizes", None)
        self._font_size_defaults = {}
        self._sync_slide_font_sizes()
        self._update_edit()
        self._save_preferences()

    def _update_edit(self):
        self._edit_job = None
        self._flush_slide()
        if not self.deck:
            self._save_preferences()
        self._sync_slide_font_sizes()
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
        output_width, output_height = self._current_slide_size()
        height = max(120, min(round(480 * self._get_widget_scaling()), round(width * output_height / output_width)))
        self.preview.configure(height=height)
        self.preview.delete("all")
        if not self.deck or not self.deck.get("slides"):
            self.preview.create_text(width // 2, height // 2, text="PPT 스타일 슬라이드 미리보기", fill=self.palette["muted"])
            return
        rendered = render_slide(self.deck["slides"][self.slide_index], self.theme.get(), self.slide_index + 1,
                                len(self.deck["slides"]), self.show_numbers.get(), self._output_size,
                                show_photo_sources=self.show_photo_sources.get())
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
        size = deck_slide_size(deck)
        deck_slide_sizes(deck)
        if assign_deck_themes(deck):
            self.store.save_deck(deck)
        if self._edit_job:
            self.after_cancel(self._edit_job)
            self._edit_job = None
        self.deck, self.slide_index = deck, 0
        self._output_size = size
        for entry, value in zip(self.size_entries, size):
            entry.delete(0, "end")
            entry.insert(0, str(value))
        self._update_size_labels()
        self.keyword_entry.delete(0, "end")
        self.keyword_entry.insert(0, deck.get("keyword", ""))
        self.title_entry.delete(0, "end")
        self.title_entry.insert(0, deck.get("title", ""))
        self.body_box.delete("1.0", "end")
        self.body_box.insert("1.0", deck.get("body", ""))
        self.theme.set(deck.get("theme", "스카이"))
        self.show_numbers.set(bool(deck.get("show_numbers", self.show_numbers.get())))
        self.show_photo_sources.set(bool(deck.get("show_photo_sources", self.show_photo_sources.get())))
        if deck.get("selection"):
            self.refresh_connections({**deck["selection"], "blog_preferences": self._connection_preferences})
        self.deck_title.configure(text=deck.get("title", "새 작업"))
        self._refresh_photo_menu()
        self._refresh_deck_menu()
        self._show_slide()
        self._save_preferences()
        if self.embedded:
            self.article_source_label.configure(text="원본 글: " + deck.get("title", "")
                                                 + "\n사진 검색어: " + deck_wiki_keyword(deck) + "\n글 내용은 ③에서 수정하세요.")

    def _refresh_photo_menu(self):
        self._image_labels = {f"{index}. {asset.get('source', '내 이미지')} · {asset.get('title', '')[:30]}": asset
                              for index, asset in enumerate((self.deck or {}).get("images", []), 1)}
        self.photo_menu.configure(values=["사진 없음", *self._image_labels])

    def _show_slide(self):
        slides = (self.deck or {}).get("slides", [])
        if not slides:
            self.page_label.configure(text="0 / 0")
            self._draw_preview()
            self._sync_slide_font_sizes()
            return
        slide = slides[self.slide_index]
        self._sync_slide_size_entries()
        self.photo_menu.configure(state="normal" if self.slide_index < 2 else "disabled")
        self.layout_menu.configure(state="normal" if self.slide_index < 2 else "disabled")
        for widget, key in ((self.eyebrow_entry, "eyebrow"), (self.slide_title_box, "title"), (self.summary_box, "summary"), (self.bullets_box, "bullets")):
            start = 0 if isinstance(widget, ctk.CTkEntry) else "1.0"
            widget.delete(start, "end")
            value = "\n".join(slide.get(key, [])) if key == "bullets" else slide.get(key, "")
            widget.insert(start, value)
        self.layout_menu.set(slide.get("layout", "사진 오른쪽"))
        self._sync_slide_font_sizes()
        for key, choices in (("text_horizontal", TEXT_HORIZONTAL_POSITIONS), ("text_vertical", TEXT_VERTICAL_POSITIONS)):
            value = slide.get(key, choices[0])
            self.text_position_menus[key].set(value if value in choices else choices[0])
        asset = slide.get("image") or {}
        self.photo_menu.set(next((label for label, item in self._image_labels.items() if item.get("path") == asset.get("path")), "사진 없음"))
        self.source_label.configure(text=f"{asset.get('source', '사진 없음')} · {asset.get('title', '')}\n{asset.get('source_page', '')}".strip())
        theme_label = f" · {slide['color_theme']}" if self.theme.get() == "랜덤" else ""
        self.page_label.configure(text=f"{self.slide_index + 1} / {len(slides)}{theme_label}")
        self._draw_preview()

    def _move(self, offset):
        if self.busy or not self.deck or not self.deck["slides"]:
            return
        self._flush_slide()
        self.slide_index = (self.slide_index + offset) % len(self.deck["slides"])
        self._show_slide()

    def _choose_photo(self, label):
        if self.busy or not self.deck or not self.deck["slides"] or self.slide_index >= 2:
            return
        self._flush_slide()
        slide = self.deck["slides"][self.slide_index]
        slide["image"] = copy.deepcopy(self._image_labels.get(label))
        slide["layout"] = ("사진 왼쪽" if self.slide_index == 0 else "사진 오른쪽") if slide["image"] else "공통 스타일"
        self.store.save_deck(self.deck)
        self._show_slide()

    def _local_photo(self):
        if self.busy or not self.deck or not self.deck["slides"]:
            return
        if self.slide_index >= 2:
            self.status.configure(text="사진은 1·2번 슬라이드에만 넣습니다. 앞의 두 장을 선택해 주세요.")
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
            self.deck["slides"][self.slide_index]["layout"] = "사진 왼쪽" if self.slide_index == 0 else "사진 오른쪽"
            self.store.save_deck(self.deck)
            self._refresh_photo_menu()
            self._show_slide()
        except (OSError, ValueError) as exc:
            messagebox.showerror("이미지 첨부", str(exc), parent=self.app)

    def _reset_slide(self):
        if self.busy or not self.deck or not self.deck["slides"]:
            return
        restored = (build_summary_slides(self.deck["title"], self.deck["body"], self.deck["summary_slides"])
                    if self.deck.get("summary_slides") else build_slides(self.deck["title"], self.deck["body"], 10))
        positions = text_position_defaults(self.deck)
        apply_text_positions(restored, positions["text_horizontal"], positions["text_vertical"])
        assign_slide_images(restored, self.deck.get("images", []))
        for key in ("color_theme", "slide_size", "font_sizes"):
            if key in self.deck["slides"][self.slide_index]:
                restored[self.slide_index][key] = copy.deepcopy(self.deck["slides"][self.slide_index][key])
        self.deck["slides"][self.slide_index] = restored[self.slide_index]
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
        body = self.app.article_editor.get("1.0", "end").strip()
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

    def _publishing(self):
        if not self.embedded:
            return False
        return any(worker is not None and worker.is_alive()
                   for worker in (getattr(self.app, "pipeline_worker", None),
                                  getattr(self.app, "tistory_automation_worker", None)))

    def offer_article(self):
        """Show current source status without discarding a saved/edited deck."""
        if not self.embedded or self.busy:
            return
        source = self.article_provider()
        self.article_source_label.configure(text="현재 글: " + (source["title"] or "③에서 글을 완성해 주세요.")
                                             + "\n사진 검색어: " + (source.get("wiki_keyword") or source["keyword"])
                                             + "\n글은 ③에서, 사진 검색어는 ①에서 수정하세요.")
        from tistory_v2_publish import deck_matches_article
        if self.deck and not deck_matches_article(self.deck, **source):
            self.status.configure(text="저장한 슬라이드를 표시하고 있습니다. 현재 글로 바꾸려면 ‘10장 만들기’를 누르세요. 기존 작업은 저장 목록에 유지됩니다.")

    def prepare_article(self, confirm=True):
        if self.busy or self._publishing():
            return False
        source = self.article_provider()
        if not plain_text(source["article_html"]).strip():
            messagebox.showinfo("본문 필요", "③ 글 확인·수정에서 글을 먼저 완성해 주세요.", parent=self.app)
            return False
        if self.deck and confirm and not messagebox.askyesno(
                "새 슬라이드 만들기", "현재 본문으로 10장을 새로 만들까요?\n지금 편집한 작업은 저장 목록에 그대로 보관됩니다.", parent=self.app):
            return False
        self._start("create", source=source)
        return True

    def snapshot_for_publish(self):
        """Flush even a still-pending keyboard edit before freezing the deck."""
        if self.busy:
            raise ValueError("슬라이드 작업이 끝난 뒤 업로드해 주세요.")
        if self._edit_job:
            self.after_cancel(self._edit_job)
            self._edit_job = None
        self._commit_image_size()
        self._commit_slide_image_size()
        from tistory_v2_publish import deck_matches_article
        if not deck_matches_article(self.deck, **self.article_provider()):
            raise ValueError("현재 본문 또는 사진 검색어가 슬라이드와 다릅니다. 본문이 바뀌면 ‘10장 만들기’, 사진 검색어만 바뀌면 ‘사진 수집’을 눌러 주세요.")
        return copy.deepcopy(self.deck)

    def _set_busy(self, busy):
        for control in self._controls:
            control.configure(state="disabled" if busy else "normal")
        if self.embedded:
            for menu in self.app.tistory_v2_controls.menus.values():
                menu.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        if not busy:
            self._browser_open = False
            self.cancel_button.configure(text="작업 취소")
        if busy:
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.set(0)

    def _cancel_work(self):
        if self._browser_open and not messagebox.askyesno("티스토리 창 닫기", "티스토리 Chrome을 닫을까요? 편집한 글은 티스토리에서 저장한 뒤 닫아 주세요.", parent=self.app):
            return
        self.cancel.set()
        self.cancel_button.configure(state="disabled")
        self.status.configure(text="취소 요청을 보냈습니다. 현재 처리 중인 작업이 끝나면 중단합니다.")

    def reset_for_new_article(self):
        """Detach this run's late events; retain saved decks and design defaults."""
        self.cancel.set()
        old_worker = self.worker
        self._flush_slide()
        if self._edit_job:
            self.after_cancel(self._edit_job)
            self._edit_job = None
        if self._font_preview_job:
            self.after_cancel(self._font_preview_job)
            self._font_preview_job = None
        self.events = queue.Queue()
        self.worker = None
        self._publish_locked = False
        self._set_busy(False)
        self.deck, self.slide_index = None, 0
        for entry in (self.keyword_entry, self.title_entry, self.eyebrow_entry):
            entry.delete(0, "end")
        for box in (self.body_box, self.slide_title_box, self.summary_box, self.bullets_box):
            box.delete("1.0", "end")
        self.deck_title.configure(text="새 슬라이드 작업")
        self.page_label.configure(text="0 / 0")
        self.source_label.configure(text="사진 없음")
        self._refresh_photo_menu()
        self.photo_menu.set("사진 없음")
        self._refresh_deck_menu()
        self.saved_menu.set("저장한 작업 선택" if self._deck_labels else "저장한 작업 없음")
        self._update_size_labels()
        self._sync_slide_size_entries()
        self._sync_slide_font_sizes()
        self._draw_preview()
        self._save_preferences()
        preferences = self.store.preferences()
        preferences.pop("article_summary_plan", None)
        self.store.save_preferences(preferences)
        if self.embedded:
            self.article_source_label.configure(text="원본 글: ③에서 새 글을 완성해 주세요.")
        self.status.configure(text="현재 작업을 중단했습니다. 저장한 슬라이드는 목록에서 다시 불러올 수 있습니다.")
        return old_worker

    def _start(self, operation, destination=None, source=None):
        if self.busy or self._publishing():
            return
        if self.embedded and operation == "create" and source is None:
            self.prepare_article()
            return
        if self.embedded and operation == "collect":
            source = self.article_provider()
            if not self.deck and not plain_text(source["article_html"]).strip():
                messagebox.showinfo("본문 필요", "③ 글 확인·수정에서 글을 먼저 완성해 주세요.", parent=self.app)
                return
        try:
            self._commit_image_size()
            self._commit_slide_image_size()
        except ValueError as exc:
            messagebox.showerror("이미지 크기", str(exc), parent=self.app)
            return
        keyword = (self.deck["keyword"] if operation == "collect" and self.deck else
                   source["keyword"] if source else self.keyword_entry.get().strip())
        wiki_keyword = (str(source.get("wiki_keyword") or "").strip() or keyword) if source else keyword
        automatic = self.mode.get() == "자동 모드"
        if operation in {"collect", "create"} and not keyword:
            messagebox.showinfo("키워드 입력", "사진을 검색할 키워드를 먼저 입력해 주세요.", parent=self.app)
            return
        if operation not in {"collect", "create"} and (not self.deck or not self.deck.get("slides")):
            messagebox.showinfo("슬라이드 없음", "슬라이드를 먼저 만들어 주세요.", parent=self.app)
            return
        if operation == "create" and not automatic and not plain_text(source["article_html"] if source else self.body_box.get("1.0", "end")).strip():
            messagebox.showinfo("본문 입력", "수동 모드에서는 슬라이드로 만들 본문을 먼저 입력해 주세요.", parent=self.app)
            return
        snapshot = None
        selection = self._selection()
        try:
            if operation == "create" and automatic and not self.embedded:
                if not self.workflow:
                    raise ValueError("글쓰기 연결을 찾지 못했습니다.")
                snapshot = self.workflow.snapshot(selection, generation=True)
            elif operation == "create" and self.embedded and not source.get("slide_summaries"):
                snapshot = self.workflow.snapshot(selection, generation=True)
            elif operation == "create" and not self.embedded:
                snapshot = self.workflow.snapshot(selection, generation=True)
            elif operation == "editor":
                if not self.workflow:
                    raise ValueError("티스토리 입력 연결을 찾지 못했습니다.")
                if len(self.deck["slides"]) != 10:
                    raise ValueError("슬라이드 10장을 다시 만든 뒤 티스토리에 입력해 주세요.")
                snapshot = self.workflow.snapshot(selection, editor=True, title=self.deck.get("title", ""))
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("티스토리 V2 설정 확인", str(exc), parent=self.app)
            return
        if operation in {"collect", "create"}:
            deck = (copy.deepcopy(self.deck) if operation == "collect" and self.deck
                    else self.store.new_deck(keyword, source["title"] if source else self.title_entry.get().strip(),
                                            source["article_html"] if source else self.body_box.get("1.0", "end").strip(), 10, self.theme.get()))
            deck["wiki_keyword"] = wiki_keyword
            if (operation == "create" and self.deck and self.deck.get("keyword") == keyword
                    and deck_wiki_keyword(self.deck) == wiki_keyword):
                deck["images"] = copy.deepcopy(self.deck.get("images", []))
        else:
            deck = copy.deepcopy(self.deck)
        deck["selection"] = {key: value for key, value in selection.items() if key != "blog_preferences"}
        deck["show_numbers"] = self.show_numbers.get()
        deck["show_photo_sources"] = self.show_photo_sources.get()
        deck["slide_size"] = list(self._output_size)
        if operation == "create":
            deck["text_position_defaults"] = dict(self._text_position_defaults)
            deck["font_size_defaults"] = dict(self._font_size_defaults)
        visible = self.show_browser.get()
        self.cancel = threading.Event()
        cancel, events, store, provider = self.cancel, self.events, self.store, self.chrome_path_provider
        self._set_busy(True)
        self.status.configure(text="작업을 준비합니다...")

        def run():
            warnings, paths = [], []
            report = lambda message: events.put(("progress", message))
            try:
                if operation == "create" and automatic and not self.embedded:
                    generated = self.workflow.generate(snapshot, keyword, deck["body"], cancel, report)
                    deck.update(title=generated["title"], body=generated["html"], provider=generated.get("provider", ""))
                    summary_rows = generated["slide_summaries"]
                elif operation == "create":
                    summary_rows = source.get("slide_summaries") if source else None
                    if not summary_rows:
                        summary_rows = self.workflow.summarize(snapshot, deck["title"], deck["body"], cancel, report)
                if operation == "create":
                    deck["summary_slides"] = copy.deepcopy(summary_rows)
                    deck["text_mode"] = "summary"
                    deck["slides"] = build_summary_slides(deck["title"], deck["body"], summary_rows)
                    positions = text_position_defaults(deck)
                    apply_text_positions(deck["slides"], positions["text_horizontal"], positions["text_vertical"])
                    apply_slide_font_sizes(deck["slides"], deck.get("font_size_defaults", {}))
                    assign_deck_themes(deck)
                if operation == "collect" or (operation == "create" and automatic):
                    try:
                        deck["images"], warnings = collect_wiki_images(wiki_keyword, store.deck_directory(deck) / "images", provider(), cancel,
                                                                      report, visible)
                    except (Cancelled, MemoryError):
                        raise
                    except Exception as exc:
                        if not self.embedded:
                            raise
                        warnings = [f"위키 사진 수집 실패: {str(exc)[:160]} · 사진은 직접 선택할 수 있습니다."]
                if operation in {"collect", "create"}:
                    assign_slide_images(deck["slides"], deck["images"])
                if operation != "collect":
                    paths = export_deck(store, deck, cancel, report)
                else:
                    store.save_deck(deck)
                if operation == "editor":
                    self.workflow.fill_editor(snapshot, deck, paths, store.deck_directory(deck), cancel, report,
                                              lambda kind, message: events.put((kind, message)))
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
        publishing = self._publishing()
        if publishing != self._publish_locked and not self.busy:
            self._publish_locked = publishing
            self._set_busy(publishing)
            self.cancel_button.configure(state="disabled")
            if not publishing and self.deck:
                self._show_slide()
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self.status.configure(text=payload)
                    continue
                if kind in {"editor_ready", "editor_attention"}:
                    self._browser_open = True
                    self.progress.stop()
                    self.progress.set(1 if kind == "editor_ready" else 0)
                    self.cancel_button.configure(text="브라우저 닫기")
                    self.status.configure(text=payload)
                    continue
                self._set_busy(False)
                self.worker = None
                if kind == "done":
                    operation, deck, paths, warnings, destination = payload
                    self._load_deck(deck)
                    self._refresh_deck_menu()
                    message = f"사진 {len(deck['images'])}개 · 슬라이드 {len(deck['slides'])}장 · " + ("사진 수집 완료" if operation == "collect" else f"본문용 PNG {len(paths)}장 저장 완료")
                    if operation == "editor":
                        message = "티스토리 입력·콜라주 검증 완료 · Chrome 창을 닫았습니다."
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
                if self.on_finished:
                    self.on_finished(kind == "done")
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
        output_width, output_height = self._current_slide_size()
        height = max(220, min(int(self.app.main_area.winfo_height() / self._get_widget_scaling()) - 40,
                              round((width - 48) * output_height / output_width) + 100))
        dialog = self.app._create_in_app_completion_dialog(width, height)
        canvas = tk.Canvas(dialog, highlightthickness=0, background=self.palette["panel"])
        canvas.pack(fill="both", expand=True, padx=18, pady=(18, 5))
        rendered = render_slide(self.deck["slides"][self.slide_index], self.theme.get(), self.slide_index + 1,
                                len(self.deck["slides"]), self.show_numbers.get(), self._output_size,
                                show_photo_sources=self.show_photo_sources.get())
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
            for job in (self._poll_job, self._edit_job, self._resize_job, self._font_preview_job):
                if job:
                    self.after_cancel(job)
        super().destroy()
