"""Импорт расписания: один формат — таблица занятий, разные типы файлов.

Столбцы (заголовки по-русски или по-английски, регистр не важен):

| Поле        | Заголовки                                   | Значение                                   |
| ----------- | ------------------------------------------- | ------------------------------------------ |
| date        | Дата, date                                  | 29.09.2026 или 2026-09-29 (или дата Excel) |
| start       | Начало, Время начала, start                 | 08:30                                      |
| end         | Конец, Окончание, Время окончания, end      | 10:05                                      |
| title       | Дисциплина, Предмет, Название, Занятие, title | Текст                                    |
| groups      | Группы, Группа, groups                      | Названия групп через запятую или «;»       |
| teachers    | Преподаватели, Преподаватель, teachers      | ФИО как при вступлении в вуз или UUID      |
| location    | Аудитория, Место, location                  | Текст; пусто — онлайн                      |
| description | Комментарий, Описание, description          | Текст                                      |
| status      | Статус, status                              | пусто/scheduled/проводится; cancelled/отменено |

Типы файлов: JSON (`[{...}]` или `{"events": [...]}`), Excel `.xlsx` (первый лист, первая строка —
заголовки), выгрузки 1С — CSV/TXT (разделитель «;», табуляция или запятая; UTF-8 или Windows-1251)
и XML (элементы-строки с атрибутами или вложенными элементами, названными как столбцы).
Время — местное время вуза. Модуль не знает о БД и ядре: только превращает файл в строки.
"""
import csv
import io
import json
import re
import zipfile
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional
from xml.etree import ElementTree

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000

FIELDS = {
    "date": ("дата", "date", "день"),
    "start": ("начало", "время начала", "start", "starts_at", "с"),
    "end": ("конец", "окончание", "время окончания", "end", "ends_at", "по"),
    "title": ("дисциплина", "предмет", "название", "занятие", "title", "subject"),
    "groups": ("группы", "группа", "groups", "group"),
    "teachers": ("преподаватели", "преподаватель", "teachers", "teacher"),
    "location": ("аудитория", "место", "location", "room", "кабинет"),
    "description": ("комментарий", "описание", "примечание", "description", "comment"),
    "status": ("статус", "status"),
}
ALIASES = {alias: field for field, names in FIELDS.items() for alias in names}
REQUIRED = ("date", "start", "end", "title", "groups", "teachers")
CANCELLED = {"cancelled", "canceled", "отменено", "отменена", "отмена", "отменён", "отменен"}
SCHEDULED = {"", "scheduled", "проводится", "по расписанию", "состоится"}
EXCEL_EPOCH = datetime(1899, 12, 30)


class ImportFailed(Exception):
    """Файл нельзя прочитать как таблицу занятий (тип, кодировка, структура, размер)."""


def normalize_header(value) -> Optional[str]:
    key = re.sub(r"\s+", " ", str(value or "")).strip().strip(":").lower().replace("ё", "е")
    return ALIASES.get(key)


# --------------------------------------------------------------------------
# Типы файлов → список словарей «заголовок → значение»
# --------------------------------------------------------------------------

def read_rows(filename: str, data: bytes) -> List[Dict[str, object]]:
    if len(data) > MAX_BYTES:
        raise ImportFailed("Файл больше 2 МБ — разбейте расписание на части")
    name = (filename or "").lower()
    if name.endswith(".json"):
        rows = _json_rows(data)
    elif name.endswith(".xlsx"):
        rows = _xlsx_rows(data)
    elif name.endswith((".csv", ".txt")):
        rows = _csv_rows(data)
    elif name.endswith(".xml"):
        rows = _xml_rows(data)
    elif name.endswith((".xls", ".mxl")):
        raise ImportFailed("Старый формат Excel (.xls) и табличный документ 1С (.mxl) не читаются: "
                           "сохраните файл как .xlsx или .csv")
    else:
        raise ImportFailed("Поддерживаются файлы .json, .xlsx, .csv, .txt и .xml")
    if len(rows) > MAX_ROWS:
        raise ImportFailed(f"Не больше {MAX_ROWS} занятий в одном файле")
    return rows


def _text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportFailed("Не удалось определить кодировку: сохраните файл в UTF-8 или Windows-1251")


def _json_rows(data: bytes) -> list:
    try:
        payload = json.loads(_text(data))
    except ValueError:
        raise ImportFailed("Файл не является JSON")
    if isinstance(payload, dict):
        payload = payload.get("events", payload.get("занятия"))
    if not isinstance(payload, list) or not all(isinstance(x, dict) for x in payload):
        raise ImportFailed('JSON: список занятий или объект {"events": [...]}')
    return payload


def _csv_rows(data: bytes) -> list:
    text = _text(data)
    sample = text[:4096]
    delimiter = max((";", "\t", ","), key=sample.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    table = [row for row in reader if any(cell.strip() for cell in row)]
    return _table_rows(table)


def _table_rows(table: list) -> list:
    """Первая строка, в которой узнаются обязательные столбцы, — заголовок (выше может быть шапка 1С)."""
    for i, row in enumerate(table[:20]):
        headers = [normalize_header(c) for c in row]
        if {"date", "start", "title"} <= set(headers):
            return [{h: (r[j] if j < len(r) else "") for j, h in enumerate(headers) if h} for r in table[i + 1:]]
    raise ImportFailed("Не найдена строка заголовков: нужны столбцы «Дата», «Начало», «Конец», «Дисциплина», «Группы»")


def _xml_rows(data: bytes) -> list:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        raise ImportFailed("Файл не является корректным XML")
    rows = []
    for element in root.iter():
        values = {}
        for key, value in element.attrib.items():
            field = normalize_header(key)
            if field:
                values[field] = value
        for child in element:
            field = normalize_header(child.tag)
            if field and len(child) == 0:
                values[field] = child.text or ""
        if {"date", "start", "title"} <= set(values):
            rows.append(values)
    if not rows:
        raise ImportFailed("В XML не найдены занятия: у элементов нужны поля Дата, Начало, Конец, Дисциплина, Группы")
    return rows


def _xlsx_rows(data: bytes) -> list:
    """Минимальное чтение .xlsx без внешних библиотек: общие строки и первый лист."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            for item in ElementTree.fromstring(book.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in item.iter(f"{{{ns['m']}}}t")))
        sheets = sorted(n for n in book.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            raise ImportFailed("В файле Excel нет листов")
        sheet = ElementTree.fromstring(book.read(sheets[0]))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        raise ImportFailed("Файл не является книгой Excel .xlsx")
    table = []
    for row in sheet.iter(f"{{{ns['m']}}}row"):
        cells: Dict[int, object] = {}
        for cell in row.findall("m:c", ns):
            column = _column_index(cell.get("r", ""))
            kind = cell.get("t")
            value_node = cell.find("m:v", ns)
            raw = value_node.text if value_node is not None else None
            if kind == "s" and raw is not None:
                value = shared[int(raw)]
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(f"{{{ns['m']}}}t"))
            elif kind in ("str", "b") or raw is None:
                value = raw or ""
            else:
                value = float(raw)  # число: дата или время Excel, либо просто число
            cells[column] = value
        if cells:
            width = max(cells) + 1
            table.append([cells.get(i, "") for i in range(width)])
    return _table_rows([[c if isinstance(c, float) else str(c) for c in r] for r in table])


def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "A").group(0)
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - 64
    return index - 1


# --------------------------------------------------------------------------
# Значения
# --------------------------------------------------------------------------

def parse_date(value) -> date:
    if isinstance(value, float):
        return (EXCEL_EPOCH + timedelta(days=int(value))).date()
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.split(" ")[0], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"дата «{text}» — нужен формат 29.09.2026 или 2026-09-29")


def parse_time(value) -> time:
    if isinstance(value, float):
        minutes = round((value % 1) * 24 * 60)
        return time(minutes // 60 % 24, minutes % 60)
    text = str(value or "").strip().replace(".", ":")
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", text)
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValueError(f"время «{text}» — нужен формат 08:30")
    return time(int(match.group(1)), int(match.group(2)))


def split_list(value) -> List[str]:
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[;,\n]", str(value or ""))
    return [str(x).strip() for x in items if str(x).strip()]


def parse_status(value) -> str:
    text = str(value or "").strip().lower()
    if text in CANCELLED:
        return "cancelled"
    if text in SCHEDULED:
        return "scheduled"
    raise ValueError(f"статус «{value}» — «проводится» или «отменено»")


def canonical(row: dict) -> dict:
    """Строка JSON с английскими или русскими ключами → поля формата."""
    return {field: value for key, value in row.items() if (field := normalize_header(key) or (key if key in FIELDS else None))}
