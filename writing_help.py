"""Offline, contextual writing manuals. Opening a guide never runs app actions."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk

import customtkinter as ctk
from PIL import Image

from input_surfaces import configure_changed
from modal_backdrop import modal_dimmer


WRITING_GUIDES = {
    "topic": {
        "title": "주제 입력 및 키워드 찾기", "stage": "1단계 · 키워드 찾기",
        "intro": "주제를 직접 시작하거나, 연관 키워드를 먼저 찾아볼 수 있어요.",
        "steps": (
            ("먼저 글쓰기 대상을 확인하세요", "화면 위쪽에서 블로그와 계정을 고르고, 사용할 썸네일·프롬프트를 선택하세요. 처음이라면 환경설정에서 블로그 연결과 AI 서비스 설정을 먼저 마쳐 주세요."),
            ("주제나 키워드를 입력하세요", "예: 제주도 여행 준비물. [검색]은 연관 키워드를 찾아보는 기능이고, 입력칸 오른쪽 [시작]은 입력한 키워드로 참고자료 수집과 글 작성을 이어가는 기능이에요."),
            ("추천 목록 또는 직접 시작 중 선택하세요", "[다음]·[시그널]·[뉴닉]·[네이버]·[로워드]는 각 출처의 키워드를 불러와요. [자동진행 ON]이면 다음 단계가 자동으로 이어질 수 있으니, 단계별로 검토하려면 OFF로 두세요."),
        ),
        "note": "글쓰기에는 선택한 AI 서비스의 로그인이나 API 설정이 필요해요. 서비스에 따라 사용량이 소모될 수 있어요.",
    },
    "benchmark": {
        "title": "다른사람글 벤치마킹", "stage": "1단계 · 키워드 찾기",
        "intro": "참고하고 싶은 글의 주소로 자료 수집을 시작하는 방식이에요.",
        "steps": (
            ("벤치마킹을 켜세요", "[다른사람글 벤치마킹]을 체크하면 URL을 입력할 수 있어요. 일반 주제 입력 대신 벤치마킹 방식으로 진행돼요."),
            ("글 주소를 붙여 넣으세요", "블로그 홈이 아니라 참고할 글 한 편의 전체 주소를 입력하고 [벤치마킹]을 누르세요."),
            ("수집한 내용을 검토하세요", "참고내용을 확인·보완한 뒤 글을 작성하세요. 다른 글을 그대로 복제하는 기능이 아니므로, 작성된 글의 표현과 사실관계도 직접 확인해 주세요."),
        ),
        "note": "로그인이 필요하거나 접근이 제한된 글은 수집되지 않을 수 있어요. 글과 사진의 이용 권한·출처는 별도로 확인해 주세요.",
    },
    "links": {
        "title": "본문 링크 버튼", "stage": "1단계 · 키워드 찾기",
        "intro": "독자가 눌러 이동할 버튼을 미리 구성하고, 자주 쓰는 조합을 저장할 수 있어요.",
        "steps": (
            ("버튼을 추가하세요", "[+ 추가]를 눌러 버튼명과 전체 URL을 입력하세요. 예: ‘자세히 알아보기’ + https://example.com/details. 직접 만드는 버튼은 최대 5개예요."),
            ("크기와 삽입 위치를 정하세요", "크기에는 원하는 너비를 입력하고, 가로를 채우려면 [100%]를 선택하세요. [본문상단]·[본문중간]·[본문하단] 중 위치를 고르세요."),
            ("노출 여부와 세트를 확인하세요", "[본문 노출 ON]일 때 본문에 삽입돼요. [버튼 저장]에서 세트 이름을 입력하고, 옆 드롭다운에서 저장한 세트를 불러올 수 있어요. 세트에는 노출 ON/OFF도 함께 저장돼요."),
        ),
        "note": "OFF여도 입력값은 보관돼요. 저장한 세트는 다른 블로그에서도 불러올 수 있고, 현재 입력값은 블로그별로 기억해요. 발행 전 URL이 올바른지 확인하세요.",
    },
    "keyword": {
        "title": "추천 키워드 선택", "stage": "2단계 · 참고 수집",
        "intro": "여러 추천 중 이번 글에 쓸 키워드 하나를 정하는 단계예요.",
        "steps": (
            ("추천 키워드를 살펴보세요", "목록의 문구와 표시된 정보를 보고 주제에 맞는 키워드를 골라 주세요. [확인]은 해당 키워드를 살펴볼 때 사용해요."),
            ("라디오 버튼으로 선택하세요", "글로 쓸 키워드 옆 동그라미를 선택하세요. 원하는 것이 없으면 아래 [직접 키워드 입력]을 사용해도 돼요."),
            ("필요하면 새로고침하세요", "[새로고침]은 현재 키워드 출처의 목록을 다시 불러와요. 원하는 키워드를 정한 뒤 참고내용을 수집하세요."),
        ),
        "note": "자동진행이 켜져 있으면 선택 후 수집이나 다음 단계가 이어질 수 있어요. 수동으로 확인하려면 화면 위 자동진행을 꺼 주세요.",
    },
    "manual": {
        "title": "직접 키워드 입력", "stage": "2단계 · 참고 수집",
        "intro": "추천 목록에 없는 문구로도 글을 작성할 수 있어요.",
        "steps": (
            ("원하는 키워드를 입력하세요", "이 입력칸에 이번 글에서 다룰 키워드를 적으세요. 직접 입력한 문구를 바탕으로 진행할 수 있어요."),
            ("참고내용을 확인하세요", "키워드를 바꿨다면 이전 주제의 참고내용이 남아 있지 않은지 확인하고, 새 주제로 [수집하기]를 진행하세요."),
            ("작성 전에 설정을 확인하세요", "프롬프트와 이미지 설정을 확인한 뒤 [다음: 글 작성]을 누르세요. 입력칸에서 Enter를 누르면 글 작성으로 이어질 수 있어요."),
        ),
        "note": "입력한 키워드와 참고내용의 주제가 일치해야 엉뚱한 내용의 글이 생성되는 것을 줄일 수 있어요.",
    },
    "reference": {
        "title": "참고내용", "stage": "2단계 · 참고 수집",
        "intro": "AI가 참고할 자료를 수집하고, 잘못된 정보가 없는지 확인하는 곳이에요.",
        "steps": (
            ("키워드를 정하고 수집하세요", "추천 또는 직접 입력한 키워드를 확인한 뒤 [수집하기]를 누르세요."),
            ("내용을 읽고 보완하세요", "수집된 자료는 이 입력칸에서 직접 고칠 수 있어요. 오래된 일정·가격·조건이나 광고 문구는 확인하고 필요한 사실을 보완해 주세요. [초기화]는 현재 참고내용을 비워요."),
            ("프롬프트를 고르고 글을 작성하세요", "상단 또는 참고내용 옆 프롬프트 선택을 확인하세요. 참고내용과 이미지 설정을 검토한 뒤 [다음: 글 작성]을 누르세요."),
        ),
        "note": "웹에서 수집됐다고 정확성이 보장되지는 않아요. 중요한 정보는 원문이나 공식 안내와 비교하고, 민감한 개인정보는 입력하지 마세요.",
    },
    "images": {
        "title": "이미지 삽입", "stage": "2단계 · 참고 수집",
        "intro": "본문에 이미지를 넣을지, 몇 장을 어떤 방식으로 준비할지 정해요.",
        "steps": (
            ("이미지 삽입을 켜고 장수를 정하세요", "[이미지 삽입]을 체크하고 원하는 장수를 고르세요. 일반 블로그글쓰기에서는 최대 4장까지 설정할 수 있어요."),
            ("준비 방식을 선택하세요", "내 사진을 넣으려면 직접 첨부 방식을, 본문 소제목으로 이미지를 만들려면 [카드뉴스 생성]을 선택하세요. AI 이미지 방식은 해당 서비스 설정이 필요해요."),
            ("첨부 파일이나 결과를 확인하세요", "직접 첨부는 파일 선택 또는 드래그 앤 드롭을 이용해요. 카드뉴스는 글이 완성된 뒤 본문을 바탕으로 준비되므로, 4단계에서 장별로 확인하세요."),
        ),
        "note": "이미지 삽입을 끄면 해당 자동 삽입을 사용하지 않아요. 사용할 사진의 권한과 생성된 이미지의 내용은 발행 전에 확인해 주세요.",
    },
    "article": {
        "title": "생성된 글 확인 및 수정", "stage": "3단계 · 글 확인·수정",
        "intro": "생성 결과를 검토하고 최종 글로 다듬는 단계예요.",
        "steps": (
            ("제목과 본문을 읽어 보세요", "제목 입력칸과 본문 편집칸에서 내용을 직접 수정할 수 있어요. 키워드·날짜·이름·링크가 맞는지 확인하세요."),
            ("미리보기로 독자 화면을 확인하세요", "본문은 HTML 형태로 보일 수 있어요. [미리보기]로 글의 구성과 링크를 살펴보고, HTML을 수정할 때는 태그를 지우지 않도록 주의해 주세요."),
            ("썸네일 단계로 이동하세요", "검토가 끝나면 [다음: 썸네일 제작]을 누르세요. 자동진행이 켜져 있으면 검토를 기다리지 않고 이어질 수 있으니 먼저 OFF로 두면 좋아요."),
        ),
        "note": "AI 결과에는 오류가 있을 수 있어요. 사실관계·표현·저작권을 검토한 뒤 발행 여부를 결정하세요.",
    },
    "publish": {
        "title": "썸네일·본문 카드뉴스 제작", "stage": "4단계 · 썸네일·발행",
        "intro": "썸네일과 본문 이미지를 마지막으로 확인한 뒤 업로드해요.",
        "steps": (
            ("썸네일 세트를 선택하세요", "[썸네일·카드1]~[썸네일·카드5] 중 원하는 세트를 고르세요. 디자인 변경은 [썸네일 관리]에서 할 수 있어요."),
            ("본문 카드뉴스도 확인하세요", "여러 장이면 이미지 양옆 화살표로 이전·다음 장을 확인하세요. 제목과 이미지가 맞는지, 잘린 문구는 없는지 살펴보세요."),
            ("블로그와 발행 설정을 확인하고 업로드하세요", "화면 위 선택한 계정을 다시 확인한 뒤 [썸네일 완료 후 업로드]를 누르세요. 서비스별 로그인과 저장·발행 설정에 따라 실제 공개 발행까지 진행될 수 있어요."),
        ),
        "note": "테스트하려면 환경설정에서 해당 서비스의 저장·발행 방식을 먼저 확인하세요. 일반 블로그글쓰기의 자동진행 OFF만으로 공개 발행이 방지되는 것은 아니에요.",
    },
    "thumbnail": {
        "title": "발행할 썸네일", "stage": "4단계 · 썸네일·발행",
        "intro": "글의 대표 이미지로 사용할 썸네일을 확인하는 영역이에요.",
        "steps": (
            ("상단에서 디자인 세트를 고르세요", "저장된 5개 세트 중 사용할 것을 선택하면 미리보기에 반영돼요."),
            ("문구와 잘림을 확인하세요", "현재 글 제목에 맞는지, 긴 문구가 잘리지 않았는지 확인하세요. 수정하려면 [썸네일 관리]에서 텍스트와 디자인을 조정하세요."),
            ("본문 이미지와 구분하세요", "썸네일은 대표 이미지이고, 옆의 본문 카드뉴스는 글 중간에 들어가는 이미지예요. 서로 다른 용도라 둘 다 확인하는 것이 좋아요."),
        ),
        "note": "기본 세트 지정은 이후 작업에도 영향을 줄 수 있어요. 현재 선택한 블로그와 세트가 맞는지 확인해 주세요.",
    },
    "cardnews": {
        "title": "본문 카드뉴스", "stage": "4단계 · 썸네일·발행",
        "intro": "본문 소제목과 요약으로 준비된 카드뉴스를 한 장씩 살펴봐요.",
        "steps": (
            ("이미지 양옆 화살표를 누르세요", "왼쪽은 이전 장, 오른쪽은 다음 장이에요. 현재 번호와 전체 장수가 표시되고, 첫 장·마지막 장에서는 해당 방향이 비활성화돼요."),
            ("각 장의 제목과 내용을 확인하세요", "문구·요약·배경과 글 내용이 맞는지 확인하세요. 장별 디자인은 [썸네일 관리]의 [본문 카드뉴스 제작]에서 수정할 수 있어요."),
            ("변경 후에는 생성 결과를 다시 반영하세요", "편집한 디자인을 실제 이미지로 사용하려면 관리 화면의 [카드뉴스 생성 및 본문 반영]을 실행하고 최종 본문을 확인하세요."),
        ),
        "note": "이 미리보기는 카드뉴스를 확인하는 영역이에요. 이미지 삽입 방식이 카드뉴스 생성인지도 2단계에서 함께 확인하세요.",
    },
}


class WritingHelpButton(ctk.CTkButton):
    """Round icon hit area without CTk's text-button corner padding."""

    def _create_grid(self):
        super()._create_grid()
        self.grid_columnconfigure((0, 4), minsize=0)
        if self._image_label is not None:
            self._image_label.configure(padx=0, pady=0, borderwidth=0, highlightthickness=0)


class WritingHelpDialog(ctk.CTkFrame):
    """One read-only in-app popup, with no native window placement phase."""

    def __init__(self, parent, topic: str, *, resource_dir: Path, palette: dict):
        super().__init__(getattr(parent, "main_area", parent), width=700, height=780,
                         corner_radius=22, fg_color=palette["shell"],
                         border_width=2, border_color=palette["border"])
        self._owner = parent
        self._uses_writing_design_theme = True
        self.grid_propagate(False)
        self._closing = False
        self._shown = False
        self._visibility_job = None
        self._fit_job = None
        self._palette = palette
        self._resource_dir = Path(resource_dir)
        self._resize_job = None
        self._source_image = None
        self._image_size = None
        self._photo = None
        self._text_labels = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(20, 12), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        self.heading = ctk.CTkLabel(header, text="", anchor="w", justify="left", wraplength=550,
                                   font=ctk.CTkFont(size=21, weight="bold"), text_color=palette["text"])
        self.heading.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(header, text="닫기", width=60, height=32, command=self.close,
                      fg_color=palette["button"], hover_color=palette["button_hover"],
                      text_color=palette["text"]).grid(row=0, column=1, padx=(12, 0))
        self.scroll = ctk.CTkScrollableFrame(self, fg_color=palette["shell"],
                                            scrollbar_button_color=palette["divider"],
                                            scrollbar_button_hover_color=palette["muted"])
        self.scroll.grid(row=1, column=0, padx=16, pady=(0, 8), sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)
        self.scroll.bind("<Configure>", self._schedule_layout, add="+")
        self.footer = ctk.CTkLabel(self, text="도움말은 입력값을 바꾸지 않으며, 진행 중인 작업을 중단하지 않습니다.",
                                  text_color=palette["muted"], font=ctk.CTkFont(size=12), wraplength=600)
        self.footer.grid(row=2, column=0, padx=24, pady=(4, 16), sticky="ew")
        self._escape_tag = f"WritingHelpEscape:{id(self)}"
        tk.Misc.bind_class(self, self._escape_tag, "<Escape>", lambda _event: self.close())
        tk.Misc.bind(self, "<Destroy>", self._cancel_jobs, add="+")
        tk.Misc.bind(self, "<Unmap>", self._on_unmap, add="+")
        self._parent_configure_binding = tk.Misc.bind(self.master, "<Configure>", self._schedule_fit, add="+")
        self.show_topic(topic)
        self._activate_job = self.after_idle(self._activate)

    def _activate(self):
        self._activate_job = None
        if not self._closing and self.winfo_exists():
            self._owner.update_idletasks()
            if self._closing or not self.winfo_exists():
                return
            if not self.master.winfo_ismapped():
                self._activate_job = self.after(100, self._activate)
                return
            self._fit_to_parent()
            if self._resize_job is not None:
                self.after_cancel(self._resize_job)
            self._layout()
            modal_dimmer(self._owner).attach(self)
            self._shown = True
            self.place(relx=0.5, rely=0.5, anchor="center")
            self.lift()
            self.update_idletasks()
            if not self._closing and self.winfo_exists():
                self.grab_set()
                self.focus_set()

    def _fit_to_parent(self):
        scale = self._get_widget_scaling()
        width = max(280, min(700, int(self.master.winfo_width() / scale) - 32))
        height = max(320, min(780, int(self.master.winfo_height() / scale) - 32))
        configure_changed(self, width=width, height=height)

    def _schedule_fit(self, event):
        if event.widget is self.master and self._shown and not self._closing:
            if self._fit_job is not None:
                self.after_cancel(self._fit_job)
            self._fit_job = self.after(30, self._apply_fit)

    def _apply_fit(self):
        self._fit_job = None
        if not self._closing:
            self._fit_to_parent()
            self._schedule_layout()

    def _on_unmap(self, event):
        if event.widget is self and self._shown and not self._closing:
            if self._visibility_job is not None:
                self.after_cancel(self._visibility_job)
            self._visibility_job = self.after(60, self._close_if_hidden)

    def _close_if_hidden(self):
        self._visibility_job = None
        if not self._closing and self.winfo_exists() and not self.winfo_ismapped():
            self.close()

    def show_topic(self, topic: str):
        guide = WRITING_GUIDES[topic]
        self.topic = topic
        self.heading.configure(text=guide["title"])
        for child in self.scroll.winfo_children():
            child.destroy()
        self._text_labels = []
        self._source_image = None
        self._photo = None
        self._image_size = None
        palette = self._palette

        def label(text, row, *, color=None, bold=False):
            widget = ctk.CTkLabel(self.scroll, text=text, anchor="w", justify="left", wraplength=560,
                                  font=ctk.CTkFont(size=14, weight="bold" if bold else "normal"),
                                  text_color=color or palette["text"])
            widget.grid(row=row, column=0, padx=10, pady=(0, 12), sticky="ew")
            self._text_labels.append(widget)
            return widget

        label(guide["stage"], 0, color=palette["accent"], bold=True)
        label(guide["intro"], 1)
        self.image_label = ctk.CTkLabel(self.scroll, text="", fg_color=palette["panel"], corner_radius=12)
        self.image_label.grid(row=2, column=0, padx=10, pady=(0, 8), sticky="ew")
        path = self._resource_dir / "assets" / "writing-guide" / f"{topic}.png"
        try:
            with Image.open(path) as source:
                self._source_image = source.convert("RGB").copy()
        except (OSError, ValueError):
            self.image_label.configure(text="예시 이미지를 불러오지 못했어요. 아래 설명으로 사용 방법을 확인해 주세요.",
                                       text_color=palette["muted"], wraplength=520)
        label("예시 화면 · 글과 계정 정보는 설명을 위한 샘플입니다.", 3, color=palette["muted"])
        for index, (title, body) in enumerate(guide["steps"], start=1):
            label(f"{index}. {title}", index * 2 + 2, bold=True)
            label(body, index * 2 + 3)
        label("알아두세요\n" + guide["note"], 10, color=palette["subtext"])
        pending = [self]
        while pending:
            child = pending.pop()
            tags = list(child.bindtags())
            if self._escape_tag not in tags:
                child.bindtags((self._escape_tag, *tags))
            pending.extend(child.winfo_children())
        self.scroll._parent_canvas.yview_moveto(0)
        self._schedule_layout()

    def _schedule_layout(self, _event=None):
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(100, self._layout)

    def _layout(self):
        self._resize_job = None
        available = max(260, int(self.scroll.winfo_width() / self.scroll._get_widget_scaling()) - 32)
        if not self.scroll.winfo_ismapped():
            available = max(260, self.cget("width") - 84)
        for widget in self._text_labels:
            if widget.cget("wraplength") != available:
                widget.configure(wraplength=available)
        configure_changed(self.heading, wraplength=max(220, available - 65))
        configure_changed(self.footer, wraplength=available)
        configure_changed(self.image_label, wraplength=available)
        if self._source_image is not None:
            width = min(available, 900)
            height = max(1, round(width * self._source_image.height / self._source_image.width))
            if height > 280:
                width = max(1, round(width * 280 / height))
                height = 280
            if self._image_size != (width, height):
                self._photo = ctk.CTkImage(light_image=self._source_image, dark_image=self._source_image, size=(width, height))
                self.image_label.configure(image=self._photo, text="")
                self._image_size = (width, height)

    def _cancel_jobs(self, event=None):
        if event is not None and event.widget is not self:
            return
        for name in ("_activate_job", "_fit_job", "_resize_job", "_visibility_job"):
            job = getattr(self, name, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        binding = getattr(self, "_parent_configure_binding", None)
        if binding is not None:
            try:
                tk.Misc.unbind(self.master, "<Configure>", binding)
            except tk.TclError:
                pass
            self._parent_configure_binding = None
        try:
            tk.Misc.unbind_class(self, self._escape_tag, "<Escape>")
        except tk.TclError:
            pass

    def close(self):
        if self._closing:
            return
        self._closing = True
        self._cancel_jobs()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        dimmer = getattr(self._owner, "_modal_dimmer", None)
        if dimmer is not None:
            dimmer.detach(self)
