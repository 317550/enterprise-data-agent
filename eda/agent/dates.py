"""Conservative, reproducible Chinese date windows; no wall-clock inference."""

import calendar
import datetime as dt
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DateWindow:
    start_date: str | None = None
    end_date: str | None = None
    issue: str | None = None
    warnings: tuple[str, ...] = ()


def strict_date(value: str) -> dt.date:
    date = dt.date.fromisoformat(value)
    if date.isoformat() != value:
        raise ValueError("invalid date format")
    return date


def resolve_dates(question: str, reference_date: str) -> DateWindow:
    reference = strict_date(reference_date)
    try:
        relative = [word for word in ("今年", "去年", "上个月", "本月", "这个月") if word in question]
        explicit = re.findall(r"(?<!\d)\d{4}-\d{1,2}-\d{1,2}(?!\d)", question)
        chinese = re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]", question)
        months = re.findall(r"(\d{4})年(\d{1,2})月", question)
        years = re.findall(r"(?<!\d)(\d{4})年", question)
        # Do not turn an incomplete range into a single-day or full-month query.
        without_full_dates = re.sub(r"\d{4}年\d{1,2}月\d{1,2}[日号]", "", question)
        if re.search(r"\d{1,2}[日号]", without_full_dates):
            return DateWindow(issue="ambiguous_date")
        if (explicit or chinese) and any(word in question for word in ("至", "到")) and len(explicit or chinese) != 2:
            return DateWindow(issue="ambiguous_date")
        if chinese and (len(chinese) != len(months) or len(chinese) != len(years)):
            return DateWindow(issue="ambiguous_date")
        if explicit and (months or years):
            return DateWindow(issue="ambiguous_date")
        if re.search(r"\d{4}(?:/\d|W|\d{4}\b)", question):
            return DateWindow(issue="invalid_date")
        if len(relative) > 1 or relative and (explicit or chinese or months or years):
            return DateWindow(issue="ambiguous_date")
        warning = ()
        if relative:
            token = relative[0]
            if token == "今年":
                start, end = reference.replace(month=1, day=1), reference
                if (reference.month, reference.day) != (12, 31):
                    warning = ("今年按参考日期截至当日计算，属于未结束的年度期间。",)
            elif token == "去年":
                start, end = dt.date(reference.year - 1, 1, 1), dt.date(reference.year - 1, 12, 31)
            elif token == "上个月":
                end = reference.replace(day=1) - dt.timedelta(days=1)
                start = end.replace(day=1)
            else:
                start, end = reference.replace(day=1), reference
                if reference.day != calendar.monthrange(reference.year, reference.month)[1]:
                    warning = ("本月按参考日期截至当日计算，属于未结束的月份期间。",)
        elif explicit or chinese:
            if explicit and chinese or len(explicit or chinese) not in (1, 2):
                return DateWindow(issue="ambiguous_date")
            dates = [strict_date(value) for value in explicit] if explicit else [dt.date(*map(int, parts)) for parts in chinese]
            start, end = dates[0], dates[-1]
        elif months:
            if len(months) != 1 or len(years) != 1:
                return DateWindow(issue="ambiguous_date")
            year, month = map(int, months[0])
            start, end = dt.date(year, month, 1), dt.date(year, month, calendar.monthrange(year, month)[1])
        elif years:
            if len(years) != 1:
                return DateWindow(issue="ambiguous_date")
            year = int(years[0])
            start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
        else:
            return DateWindow(issue="missing_date")
        if start > end:
            return DateWindow(issue="invalid_date")
        if end > reference:
            warning += ("请求期间晚于参考日期；保留明确日期范围，不保证该期间数据完整。",)
        return DateWindow(start.isoformat(), end.isoformat(), warnings=warning)
    except (ValueError, OverflowError):
        return DateWindow(issue="invalid_date")
