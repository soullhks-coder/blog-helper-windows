"""Calendar and time controls for one waiting automation reservation."""
from __future__ import annotations

import calendar
import copy
from datetime import date, datetime, timedelta
import tkinter as tk

import customtkinter as ctk

from input_surfaces import InputOptionMenu
from modal_backdrop import modal_dimmer


class AutomationScheduleEditor(ctk.CTkFrame):
    def __init__(self, app, item):
        self.palette = p = app._theme_palette()
        width = min(540, max(360, int(app.main_area.winfo_width() / app.main_area._get_widget_scaling()) - 32))
        super().__init__(app.main_area, width=width, height=480, corner_radius=22,
                         fg_color=p["shell"], border_width=2, border_color=p["border"])
        self.app, self.expected, self.closed = app, copy.deepcopy(item), False
        self.item_id = str(item["id"])
        try:
            selected = datetime.fromtimestamp(float(item.get("scheduled_at") or 0))
        except (ValueError, TypeError, OverflowError, OSError):
            selected = datetime.now()
        if selected <= datetime.now():
            selected = datetime.now() + timedelta(minutes=app._automation_publish_interval_minutes())
        self.selected_date = selected.date()
        self.month = self.selected_date.replace(day=1)
        self.pack_propagate(False)
        ctk.CTkLabel(self, text="예약시간 변경", text_color=p["text"],
                     font=ctk.CTkFont(size=21, weight="bold")).pack(anchor="w", padx=24, pady=(18, 6))
        ctk.CTkLabel(self, text="날짜와 시간을 고른 뒤 저장하세요. 이 작업에만 적용됩니다.", height=22,
                     text_color=p["muted"], wraplength=width-48).pack(anchor="w", padx=24)
        quick = ctk.CTkFrame(self, fg_color="transparent")
        quick.pack(fill="x", padx=24, pady=(8, 6))
        self.quick_buttons = {}
        for col, (text, minutes) in enumerate((("10분 뒤", 10), ("30분 뒤", 30), ("1시간 뒤", 60),
                       ("등록 간격 뒤", app._automation_publish_interval_minutes()))):
            quick.grid_columnconfigure(col, weight=1)
            button = ctk.CTkButton(quick, text=text, width=70, height=26, corner_radius=10,
                fg_color=p["button"], text_color=p["text"], hover_color=p["button_hover"],
                command=lambda value=minutes: self.after_minutes(value))
            button.grid(row=0, column=col, padx=(0, 4) if col < 3 else 0, sticky="ew")
            self.quick_buttons[text] = button
        month_row = ctk.CTkFrame(self, fg_color="transparent")
        month_row.pack(fill="x", padx=24)
        month_row.grid_columnconfigure(1, weight=1)
        for col, text, delta in ((0, "‹", -1), (2, "›", 1)):
            ctk.CTkButton(month_row, text=text, width=34, height=26, fg_color=p["button"],
                text_color=p["text"], command=lambda value=delta: self.move_month(value)).grid(row=0, column=col)
        self.month_label = ctk.CTkLabel(month_row, text="", height=26, text_color=p["text"],
                                       font=ctk.CTkFont(size=15, weight="bold"))
        self.month_label.grid(row=0, column=1)
        self.calendar_frame = ctk.CTkFrame(self, fg_color=p["input"], corner_radius=12,
                                         border_width=1, border_color=p["border"])
        self.calendar_frame.pack(fill="x", padx=24, pady=(4, 6))
        times = ctk.CTkFrame(self, fg_color="transparent")
        times.pack(fill="x", padx=24)
        ctk.CTkLabel(times, text="시간", height=28, text_color=p["text"]).pack(side="left", padx=(0, 10))
        self.hour_menu = self.menu(times, [f"{i:02d}시" for i in range(24)])
        self.minute_menu = self.menu(times, [f"{i:02d}분" for i in range(60)])
        self.hour_menu.pack(side="left", padx=(0, 8))
        self.minute_menu.pack(side="left")
        self.hour_menu.set(f"{selected.hour:02d}시")
        self.minute_menu.set(f"{selected.minute:02d}분")
        self.preview = ctk.CTkLabel(self, text="", height=22, text_color=p["accent"],
                                   font=ctk.CTkFont(size=13, weight="bold"))
        self.preview.pack(fill="x", padx=24, pady=(6, 0))
        self.error = ctk.CTkLabel(self, text="재실행하면 저장한 등록 간격을 기준으로 다시 배정됩니다.",
            text_color=p["muted"], height=22, wraplength=width-48, justify="left")
        self.error.pack(fill="x", padx=24)
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(side="bottom", fill="x", padx=24, pady=(8, 18))
        self.save_button = ctk.CTkButton(actions, text="예약시간 저장", height=34, corner_radius=12,
            fg_color=p["accent"], text_color="#ffffff", command=self.save)
        self.save_button.pack(side="right")
        self.cancel_button = ctk.CTkButton(actions, text="취소", width=80, height=34, corner_radius=12,
            fg_color=p["button"], text_color=p["text"], command=self.close)
        self.cancel_button.pack(side="right", padx=8)
        self.render_calendar()
        self.place(relx=.5, rely=.5, anchor="center")
        app.__dict__.setdefault("_automation_keyword_editing_ids", set()).add(self.item_id)
        app.automation_schedule_dialog = self
        tk.Misc.bind(self, "<Destroy>", self.destroyed, add="+")
        self.bind("<Escape>", lambda _event: self.close())
        modal_dimmer(app).attach(self)
        self.lift()
        self.grab_set()
        self.focus_set()

    def menu(self, master, values):
        p = self.palette
        return InputOptionMenu(master, values=values, width=96, height=28, corner_radius=10,
            fg_color=p["button"], button_color=p["button"], text_color=p["text"],
            button_hover_color=p["button_hover"], dropdown_fg_color=p["panel"],
            dropdown_text_color=p["text"], dropdown_hover_color=p["hover"],
            command=lambda _value: self.update_preview())

    def render_calendar(self):
        for child in self.calendar_frame.winfo_children():
            child.destroy()
        self.month_label.configure(text=f"{self.month.year}년 {self.month.month}월")
        self.day_buttons = {}
        p = self.palette
        for col, text in enumerate("월화수목금토일"):
            self.calendar_frame.grid_columnconfigure(col, weight=1, uniform="day")
            ctk.CTkLabel(self.calendar_frame, text=text, height=20,
                        text_color=p["muted"]).grid(row=0, column=col, sticky="ew")
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(self.month.year, self.month.month)
        for row in range(6):
            for col in range(7):
                day = weeks[row][col] if row < len(weeks) else 0
                if not day:
                    ctk.CTkLabel(self.calendar_frame, text="", height=22).grid(row=row+1, column=col)
                    continue
                selected = self.selected_date == date(self.month.year, self.month.month, day)
                button = ctk.CTkButton(self.calendar_frame, text=str(day), height=22, width=30,
                    corner_radius=8, fg_color=p["accent"] if selected else p["input"],
                    text_color="#ffffff" if selected else p["text"], hover_color=p["button_hover"],
                    state="disabled" if date(self.month.year, self.month.month, day) < date.today() else "normal",
                    command=lambda value=day: self.select_day(value))
                button.grid(row=row+1, column=col, padx=2, pady=1, sticky="ew")
                self.day_buttons[day] = button
        self.update_preview()

    def move_month(self, delta):
        index = self.month.year * 12 + self.month.month - 1 + delta
        year, month = divmod(index, 12)
        if 1 <= year <= 9999:
            self.month = date(year, month + 1, 1)
            self.render_calendar()

    def select_day(self, day):
        self.selected_date = date(self.month.year, self.month.month, day)
        self.render_calendar()

    def after_minutes(self, minutes):
        selected = datetime.now() + timedelta(minutes=minutes)
        self.selected_date = selected.date()
        self.month = self.selected_date.replace(day=1)
        self.hour_menu.set(f"{selected.hour:02d}시")
        self.minute_menu.set(f"{selected.minute:02d}분")
        self.render_calendar()

    def selected_datetime(self):
        return datetime.combine(self.selected_date, datetime.min.time()).replace(
            hour=int(self.hour_menu.get()[:-1]), minute=int(self.minute_menu.get()[:-1]))

    def update_preview(self):
        self.preview.configure(text=self.selected_datetime().strftime("%Y년 %m월 %d일  %H:%M"))

    def save(self):
        try:
            self.app._save_automation_queue_schedule(self.item_id, self.expected,
                                                      self.selected_datetime().timestamp())
        except (ValueError, OSError, RuntimeError) as exc:
            self.error.configure(text=str(exc), text_color="#df5252")
            return
        self.close()

    def destroyed(self, event):
        if event.widget is self:
            self.release_hold()

    def release_hold(self):
        self.app.__dict__.get("_automation_keyword_editing_ids", set()).discard(self.item_id)
        if self.app.__dict__.get("automation_schedule_dialog") is self:
            self.app.automation_schedule_dialog = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.grab_release()
        self.destroy()
        modal_dimmer(self.app).detach(self)
        self.release_hold()
        self.app._finish_theme_paint(force=True)
