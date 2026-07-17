import email
import html
import imaplib
import json
import os
import re
import ssl
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_IMPORTANT_KEYWORDS = (
    "оплат",
    "платеж",
    "платёж",
    "счет",
    "счёт",
    "квитанц",
    "задолж",
    "долг",
    "штраф",
    "налог",
    "фнс",
    "госуслуг",
    "банк",
    "карта",
    "подписк",
    "списан",
    "продлен",
    "продлён",
    "истека",
    "дедлайн",
    "deadline",
    "invoice",
    "payment",
    "bill",
    "receipt",
    "subscription",
    "renewal",
    "overdue",
    "due",
    "contract",
    "договор",
    "документ",
    "sektoschool",
    "sektaschool",
    "sekto school",
    "sekta school",
    "сектоскул",
    "промокод",
    "промо-код",
)

HARD_IMPORTANT_KEYWORDS = (
    "оплат",
    "платеж",
    "платёж",
    "счет",
    "счёт",
    "квитанц",
    "задолж",
    "штраф",
    "налог",
    "банк",
    "списан",
    "invoice",
    "payment",
    "bill",
    "receipt",
    "overdue",
    "пароль",
    "безопасност",
    "sektoschool",
    "sektaschool",
    "sekto school",
    "sekta school",
    "сектоскул",
    "промокод",
    "промо-код",
)

PROMOCODE_KEYWORDS = (
    "промокод",
    "промо-код",
    "promo code",
    "promocode",
)

SCHOOL_KEYWORDS = (
    "sektoschool",
    "sektaschool",
    "sekto school",
    "sekta school",
    "сектоскул",
)

ACTION_KEYWORDS = (
    "оплат",
    "ответ",
    "подтверд",
    "заполн",
    "подпис",
    "скач",
    "соглас",
    "deadline",
    "due",
    "confirm",
    "reply",
    "sign",
    "pay",
)

PAYMENT_KEYWORDS = (
    "оплат",
    "платеж",
    "платёж",
    "счет",
    "счёт",
    "квитанц",
    "задолж",
    "долг",
    "штраф",
    "налог",
    "фнс",
    "списан",
    "invoice",
    "payment",
    "bill",
    "receipt",
    "overdue",
    "due",
)

PROMO_KEYWORDS = (
    "скидк",
    "акци",
    "распродаж",
    "sale",
    "discount",
    "promo",
    "подарк",
    "задания недели",
    "newsletter",
)

TELEGRAM_MESSAGE_LIMIT = 3900

HELP_TEXT = """Я показываю входящие письма из Яндекс.Почты и собираю сводку.

Команды:
/digest - показать письма и сводку сейчас
/status - показать текущие настройки
/help - показать эту справку

Ещё я автоматически присылаю дайджест каждый день по расписанию.
В дайджесте у писем есть кнопки: пометить прочитанным и перенести в корзину."""


@dataclass
class AttachmentInfo:
    filename: str
    content_type: str
    size: int


@dataclass
class DigestItem:
    uid: str
    sender: str
    subject: str
    date: datetime | None
    snippet: str
    labels: list[str]
    attachments: list[AttachmentInfo]
    seen: bool


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(name, value)


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def env_bool(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return env(name, fallback).lower() in {"1", "true", "yes", "on"}


def split_keywords(value: str, fallback: Iterable[str]) -> tuple[str, ...]:
    if not value.strip():
        return tuple(fallback)
    return tuple(part.strip().lower() for part in value.split(",") if part.strip())


def decode_mime(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def message_date(message: Message) -> datetime | None:
    raw = message.get("Date")
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        text = payload.decode(charset, errors="replace")
    except LookupError:
        text = payload.decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html.unescape(text)
    return normalize_space(text)


def extract_body(message: Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            disposition = (part.get_content_disposition() or "").lower()
            if disposition == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                plain_parts.append(part_text(part))
            elif content_type == "text/html":
                html_parts.append(part_text(part))
    else:
        text = part_text(message)
        if message.get_content_type() == "text/html":
            html_parts.append(text)
        else:
            plain_parts.append(text)

    return normalize_space(" ".join(plain_parts or html_parts))


def extract_attachments(message: Message) -> list[AttachmentInfo]:
    attachments: list[AttachmentInfo] = []
    for part in message.walk():
        disposition = (part.get_content_disposition() or "").lower()
        filename = decode_mime(part.get_filename())
        if disposition != "attachment" and not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append(
            AttachmentInfo(
                filename=filename or "(без имени)",
                content_type=part.get_content_type(),
                size=len(payload),
            )
        )
    return attachments


def normalize_space(value: str) -> str:
    value = "".join(
        char
        for char in value
        if not unicodedata.category(char).startswith("C") and char != "\u2800"
    )
    return re.sub(r"\s+", " ", value).strip()


def snippet(text: str, limit: int = 240) -> str:
    text = normalize_space(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def sender_name(message: Message) -> str:
    sender = decode_mime(message.get("From"))
    return sender or "(отправитель не указан)"


def imap_settings() -> tuple[str, int, str, str, str]:
    return (
        env("YANDEX_IMAP_HOST", "imap.yandex.ru"),
        int(env("YANDEX_IMAP_PORT", "993")),
        env("YANDEX_EMAIL", required=True),
        env("YANDEX_APP_PASSWORD", required=True),
        env("YANDEX_MAILBOX", "INBOX"),
    )


def unread_only() -> bool:
    return env_bool("UNREAD_ONLY", True)


def metadata_value(metadata: bytes, name: str) -> str:
    match = re.search(rb"\b" + name.encode("ascii") + rb" ([^ )]+)", metadata)
    if not match:
        return ""
    return match.group(1).decode("ascii", errors="replace")


def metadata_flags(metadata: bytes) -> set[str]:
    match = re.search(rb"FLAGS \(([^)]*)\)", metadata)
    if not match:
        return set()
    return {
        flag.decode("ascii", errors="replace").lower()
        for flag in match.group(1).split()
    }


def parse_mailbox_list_item(item: bytes) -> tuple[set[str], str] | None:
    match = re.match(rb"\((?P<flags>[^)]*)\)\s+\"[^\"]*\"\s+(?P<name>.+)$", item)
    if not match:
        return None
    flags = {
        flag.decode("ascii", errors="replace").lower()
        for flag in match.group("flags").split()
    }
    raw_name = match.group("name").strip()
    if raw_name.startswith(b'"') and raw_name.endswith(b'"'):
        raw_name = raw_name[1:-1].replace(b'\\"', b'"').replace(b"\\\\", b"\\")
    return flags, raw_name.decode("ascii", errors="replace")


def quote_mailbox_name(mailbox_name: str) -> str:
    escaped = mailbox_name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def find_trash_mailbox(imap: imaplib.IMAP4_SSL) -> str:
    status, folders = imap.list()
    if status == "OK":
        for item in folders or []:
            parsed = parse_mailbox_list_item(item)
            if not parsed:
                continue
            flags, mailbox_name = parsed
            if "\\trash" in flags:
                return mailbox_name
    return env("YANDEX_TRASH_MAILBOX", "Trash")


def classify(
    subject: str,
    sender: str,
    body: str,
    attachments: list[AttachmentInfo],
    important_keywords: Iterable[str],
) -> list[str]:
    haystack = " ".join(
        [
            subject,
            sender,
            body,
            " ".join(item.filename for item in attachments),
            " ".join(item.content_type for item in attachments),
        ]
    ).lower()

    is_promo = any(keyword in haystack for keyword in PROMO_KEYWORDS)
    has_payment = any(keyword in haystack for keyword in PAYMENT_KEYWORDS)
    has_promocode = any(keyword in haystack for keyword in PROMOCODE_KEYWORDS)
    is_school = any(keyword in haystack for keyword in SCHOOL_KEYWORDS)

    labels: list[str] = []
    if is_school:
        labels.append("Sektoschool")
    if has_payment:
        labels.append("оплата/квитанция")
    if has_promocode:
        labels.append("промокод")
    if is_promo:
        labels.append("скидка/промо")
    if any(keyword in haystack for keyword in important_keywords):
        labels.append("важное")
    if any(keyword in haystack for keyword in ACTION_KEYWORDS):
        labels.append("нужно действие")
    if attachments:
        labels.append("есть вложения")
    return labels


def fetch_messages() -> list[DigestItem]:
    host, port, username, password, mailbox = imap_settings()
    lookback_hours = int(env("LOOKBACK_HOURS", "30"))
    max_messages = int(env("MAX_MESSAGES", "80"))
    important_keywords = split_keywords(
        env("IMPORTANT_KEYWORDS", ""),
        DEFAULT_IMPORTANT_KEYWORDS,
    )

    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    since_query = since.strftime("%d-%b-%Y")

    context = ssl.create_default_context()
    with imaplib.IMAP4_SSL(host, port, ssl_context=context) as imap:
        imap.login(username, password)
        imap.select(mailbox, readonly=True)
        search_criteria = ["SINCE", since_query]
        if unread_only():
            search_criteria.append("UNSEEN")
        status, data = imap.search(None, *search_criteria)
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")

        message_ids = (data[0] or b"").split()
        items: list[DigestItem] = []
        for message_id in message_ids[-max_messages:]:
            status, fetched = imap.fetch(message_id, "(UID FLAGS BODY.PEEK[])")
            if status != "OK" or not fetched:
                continue
            raw = None
            metadata = b""
            for part in fetched:
                if isinstance(part, tuple) and len(part) > 1:
                    metadata = part[0] if isinstance(part[0], bytes) else b""
                    raw = part[1]
                    break
            if not raw:
                continue
            uid = metadata_value(metadata, "UID") or message_id.decode("ascii", errors="replace")
            flags = metadata_flags(metadata)
            message = email.message_from_bytes(raw)
            date = message_date(message)
            if date and date < since:
                continue

            subject = decode_mime(message.get("Subject")) or "(без темы)"
            sender = sender_name(message)
            body = extract_body(message)
            attachments = extract_attachments(message)
            labels = classify(subject, sender, body, attachments, important_keywords)

            items.append(
                DigestItem(
                    uid=uid,
                    sender=sender,
                    subject=subject,
                    date=date,
                    snippet=snippet(body),
                    labels=labels,
                    attachments=attachments,
                    seen="\\seen" in flags,
                )
            )
        imap.close()
    return sorted(
        items,
        key=lambda item: item.date or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def mark_message_seen(uid: str, seen: bool = True) -> None:
    host, port, username, password, mailbox = imap_settings()
    flag_action = "+FLAGS.SILENT" if seen else "-FLAGS.SILENT"
    context = ssl.create_default_context()
    with imaplib.IMAP4_SSL(host, port, ssl_context=context) as imap:
        imap.login(username, password)
        imap.select(mailbox, readonly=False)
        status, _ = imap.uid("STORE", uid, flag_action, r"(\Seen)")
        imap.close()
    if status != "OK":
        raise RuntimeError(f"IMAP mark seen failed for UID {uid}: {status}")


def move_message_to_trash(uid: str) -> None:
    host, port, username, password, mailbox = imap_settings()
    context = ssl.create_default_context()
    with imaplib.IMAP4_SSL(host, port, ssl_context=context) as imap:
        imap.login(username, password)
        imap.select(mailbox, readonly=False)
        trash_mailbox = find_trash_mailbox(imap)
        status, _ = imap.uid("MOVE", uid, quote_mailbox_name(trash_mailbox))
        imap.close()
    if status != "OK":
        raise RuntimeError(
            f"IMAP MOVE to {trash_mailbox!r} failed for UID {uid}: {status}. "
            "Проверь папку корзины в IMAP."
        )


def format_attachment(attachment: AttachmentInfo) -> str:
    size_kb = max(1, round(attachment.size / 1024))
    return f"{attachment.filename} ({attachment.content_type}, {size_kb} KB)"


def format_digest(items: list[DigestItem]) -> str:
    if not items:
        return empty_digest_text()

    lines = [format_digest_summary(items), "", "Все письма:"]
    lines.extend(format_item(index, item) for index, item in enumerate(items, 1))
    return "\n".join(lines).strip()


def empty_digest_text() -> str:
    now = datetime.now().strftime("%d.%m.%Y")
    scope = "непрочитанных писем" if unread_only() else "писем"
    return f"Почтовый дайджест за {now}\n\nЗа выбранный период {scope} не найдено."


def digest_groups(items: list[DigestItem]) -> tuple[
    list[DigestItem],
    list[DigestItem],
    list[DigestItem],
    list[DigestItem],
    list[DigestItem],
]:
    important = [
        item
        for item in items
        if "важное" in item.labels or "нужно действие" in item.labels
    ]
    payments = [item for item in items if "оплата/квитанция" in item.labels]
    promos = [item for item in items if "скидка/промо" in item.labels or "промокод" in item.labels]
    attachments = [item for item in items if item.attachments]
    ordinary = [
        item
        for item in items
        if item not in important and item not in payments and item not in promos
    ]
    return important, payments, promos, attachments, ordinary


def format_digest_summary(items: list[DigestItem]) -> str:
    now = datetime.now().strftime("%d.%m.%Y")
    important, payments, promos, attachments, ordinary = digest_groups(items)

    lines = [
        f"Почтовый дайджест за {now}",
        f"Период: последние {env('LOOKBACK_HOURS', '30')} часов",
        f"Режим: {'только непрочитанные' if unread_only() else 'все письма'}",
        f"Показано писем: {len(items)} из лимита {env('MAX_MESSAGES', '80')}",
        "",
        "Сводка:",
        f"- Важное / нужно действие: {len(important)}",
        f"- Оплаты / счета / квитанции: {len(payments)}",
        f"- Скидки / промо / промокоды: {len(promos)}",
        f"- С вложениями: {len(attachments)}",
        f"- Остальное: {len(ordinary)}",
        "",
    ]
    if important:
        lines.append("Важно / нужно действие:")
        lines.extend(format_compact_item(index, item) for index, item in enumerate(important[:8], 1))
        if len(important) > 8:
            lines.append(f"...и ещё {len(important) - 8}")
        lines.append("")
    if payments:
        lines.append("Оплаты / счета / квитанции:")
        lines.extend(format_compact_item(index, item) for index, item in enumerate(payments[:8], 1))
        if len(payments) > 8:
            lines.append(f"...и ещё {len(payments) - 8}")
        lines.append("")
    if promos:
        lines.append("Скидки / промо / промокоды:")
        lines.extend(format_compact_item(index, item) for index, item in enumerate(promos[:8], 1))
        if len(promos) > 8:
            lines.append(f"...и ещё {len(promos) - 8}")
        lines.append("")
    lines.append("Ниже пришлю письма отдельными карточками с кнопками.")
    return "\n".join(lines).strip()


def format_compact_item(index: int, item: DigestItem) -> str:
    date = item.date.astimezone().strftime("%d.%m %H:%M") if item.date else "дата неизвестна"
    labels = ", ".join(item.labels) if item.labels else "обычное"
    return f"{index}. {date} - {item.subject} ({labels})"


def format_item(index: int, item: DigestItem) -> str:
    labels = ", ".join(item.labels) if item.labels else "обычное"
    date = item.date.astimezone().strftime("%d.%m %H:%M") if item.date else "дата неизвестна"
    read_status = "прочитано" if item.seen else "не прочитано"
    lines = [
        f"{index}. {item.subject}",
        f"   От: {item.sender}",
        f"   Когда: {date}",
        f"   Метки: {labels}",
        f"   Статус: {read_status}",
    ]
    if item.snippet:
        lines.append(f"   Кратко: {item.snippet}")
    if item.attachments:
        attachments = "; ".join(format_attachment(item) for item in item.attachments[:4])
        more = "" if len(item.attachments) <= 4 else f"; +{len(item.attachments) - 4}"
        lines.append(f"   Вложения: {attachments}{more}")
    return "\n".join(lines)


def format_item_card(index: int, item: DigestItem) -> str:
    return format_item(index, item)


def item_keyboard(item: DigestItem) -> dict[str, object]:
    buttons = []
    if not item.seen:
        buttons.append(
            {
                "text": "Пометить прочитанным",
                "callback_data": f"read:{item.uid}",
            }
        )
    buttons.append(
        {
            "text": "В корзину",
            "callback_data": f"trash:{item.uid}",
        }
    )
    return {"inline_keyboard": [buttons]}


def telegram_request(method: str, params: dict[str, str], timeout: int = 30) -> object:
    token = env("TELEGRAM_BOT_TOKEN", required=True)
    url = f"https://api.telegram.org/bot{token}/{method}"
    payload = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw)
    if not data.get("ok"):
        description = data.get("description", "unknown Telegram API error")
        raise RuntimeError(f"Telegram {method} failed: {description}")
    return data.get("result")


def send_telegram_message(
    chat_id: str | int,
    text: str,
    reply_markup: dict[str, object] | None = None,
) -> None:
    text = text or "(пустое сообщение)"
    for start in range(0, len(text), TELEGRAM_MESSAGE_LIMIT):
        chunk = text[start : start + TELEGRAM_MESSAGE_LIMIT]
        params = {
            "chat_id": str(chat_id),
            "text": chunk,
            "disable_web_page_preview": "true",
        }
        if reply_markup is not None and start + TELEGRAM_MESSAGE_LIMIT >= len(text):
            params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        telegram_request("sendMessage", params)


def send_telegram(text: str) -> None:
    chat_id = env("TELEGRAM_CHAT_ID", required=True)
    dry_run = env("DRY_RUN", "false").lower() in {"1", "true", "yes"}
    if dry_run:
        print(text)
        return

    send_telegram_message(chat_id, text)


def send_digest_to_chat(chat_id: str | int) -> None:
    items = fetch_messages()
    if not items:
        send_telegram_message(chat_id, empty_digest_text())
        return

    send_telegram_message(chat_id, format_digest_summary(items))
    for index, item in enumerate(items, 1):
        send_telegram_message(
            chat_id,
            format_item_card(index, item),
            reply_markup=item_keyboard(item),
        )


def daily_digest_time() -> datetime_time | None:
    value = env("DAILY_DIGEST_TIME", "09:00").strip()
    if value.lower() in {"", "off", "false", "none", "disabled"}:
        return None
    try:
        hour_text, minute_text = value.split(":", 1)
        return datetime_time(hour=int(hour_text), minute=int(minute_text))
    except ValueError as error:
        raise RuntimeError("DAILY_DIGEST_TIME должен быть в формате HH:MM, например 09:00") from error


def daily_digest_timezone():
    timezone_name = env("DAILY_DIGEST_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name == "Europe/Moscow":
            return timezone(timedelta(hours=3), name="Europe/Moscow")
        raise RuntimeError(f"Неизвестный часовой пояс DAILY_DIGEST_TIMEZONE={timezone_name!r}")


def initial_daily_digest_date() -> date | None:
    scheduled_time = daily_digest_time()
    if scheduled_time is None:
        return None
    now = datetime.now(daily_digest_timezone())
    if now.time() >= scheduled_time:
        return now.date()
    return None


def maybe_send_daily_digest(last_sent_date: date | None) -> date | None:
    scheduled_time = daily_digest_time()
    if scheduled_time is None:
        return None

    now = datetime.now(daily_digest_timezone())
    if last_sent_date == now.date() or now.time() < scheduled_time:
        return last_sent_date

    chat_id = env("TELEGRAM_CHAT_ID", required=True)
    send_telegram_message(
        chat_id,
        f"Доброе утро. Автоматический дайджест за {now.strftime('%d.%m.%Y')}.",
    )
    send_digest_to_chat(chat_id)
    return now.date()


def daily_digest_status() -> str:
    scheduled_time = daily_digest_time()
    if scheduled_time is None:
        return "выключен"
    timezone_name = env("DAILY_DIGEST_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
    return f"{scheduled_time.strftime('%H:%M')} {timezone_name}"


def set_telegram_commands() -> None:
    commands = [
        {"command": "digest", "description": "Показать письма и сводку"},
        {"command": "status", "description": "Показать настройки бота"},
        {"command": "help", "description": "Показать справку"},
    ]
    telegram_request(
        "setMyCommands",
        {"commands": json.dumps(commands, ensure_ascii=False)},
    )


def format_status() -> str:
    keyword_value = env("IMPORTANT_KEYWORDS", "").strip()
    keyword_source = (
        f"свой список ({len(split_keywords(keyword_value, ()))} слов)"
        if keyword_value
        else f"встроенный список ({len(DEFAULT_IMPORTANT_KEYWORDS)} слов)"
    )
    dry_run = env("DRY_RUN", "false").lower() in {"1", "true", "yes"}
    return "\n".join(
        [
            "Mail Digest Bot работает.",
            f"Папка: {env('YANDEX_MAILBOX', 'INBOX')}",
            f"IMAP: {env('YANDEX_IMAP_HOST', 'imap.yandex.ru')}:{env('YANDEX_IMAP_PORT', '993')}",
            f"Период поиска: последние {env('LOOKBACK_HOURS', '30')} часов",
            f"Режим писем: {'только непрочитанные' if unread_only() else 'все письма'}",
            f"Лимит писем: {env('MAX_MESSAGES', '80')}",
            f"Корзина: {env('YANDEX_TRASH_MAILBOX', 'Trash')}",
            f"Автодайджест: {daily_digest_status()}",
            f"Ключевые слова: {keyword_source}",
            f"DRY_RUN: {'да' if dry_run else 'нет'}",
        ]
    )


def command_name(text: str) -> str:
    if not text:
        return ""
    first = text.strip().split(maxsplit=1)[0].lower()
    if not first.startswith("/"):
        return ""
    return first.split("@", 1)[0]


def is_allowed_chat(chat_id: str | int) -> bool:
    allowed_chat_id = env("TELEGRAM_CHAT_ID", required=True).strip()
    return str(chat_id) == allowed_chat_id


def answer_callback_query(callback_query_id: str, text: str) -> None:
    text = snippet(text, limit=180)
    telegram_request(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": "false",
        },
    )


def edit_message_reply_markup(
    chat_id: str | int,
    message_id: str | int,
    reply_markup: dict[str, object],
) -> None:
    telegram_request(
        "editMessageReplyMarkup",
        {
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "reply_markup": json.dumps(reply_markup, ensure_ascii=False),
        },
    )


def handle_callback_query(callback_query: dict[str, object]) -> None:
    callback_query_id = str(callback_query.get("id") or "")
    data = str(callback_query.get("data") or "")
    message = callback_query.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat")
    if not isinstance(chat, dict) or "id" not in chat:
        return

    chat_id = chat["id"]
    message_id = message.get("message_id")
    if not is_allowed_chat(chat_id):
        if callback_query_id:
            answer_callback_query(callback_query_id, "Нет доступа.")
        return

    if ":" not in data:
        if callback_query_id:
            answer_callback_query(callback_query_id, "Не понимаю эту кнопку.")
        return

    action, uid = data.split(":", 1)
    try:
        if action == "read":
            mark_message_seen(uid, seen=True)
            if callback_query_id:
                answer_callback_query(callback_query_id, "Пометила прочитанным.")
            if message_id is not None:
                edit_message_reply_markup(
                    chat_id,
                    message_id,
                    {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "В корзину",
                                    "callback_data": f"trash:{uid}",
                                }
                            ]
                        ]
                    },
                )
            return

        if action == "trash":
            move_message_to_trash(uid)
            if callback_query_id:
                answer_callback_query(callback_query_id, "Перенесла в корзину.")
            if message_id is not None:
                edit_message_reply_markup(chat_id, message_id, {"inline_keyboard": []})
            return

        if callback_query_id:
            answer_callback_query(callback_query_id, "Не понимаю эту кнопку.")
    except Exception as error:
        print(f"callback action failed: {error}", file=sys.stderr, flush=True)
        if callback_query_id:
            answer_callback_query(callback_query_id, "Не получилось выполнить действие. Подробности в логе.")


def handle_telegram_message(message: dict[str, object]) -> None:
    chat = message.get("chat")
    if not isinstance(chat, dict) or "id" not in chat:
        return

    chat_id = chat["id"]
    if not is_allowed_chat(chat_id):
        send_telegram_message(chat_id, "Нет доступа: этот бот привязан к другому Telegram-чату.")
        return

    text = str(message.get("text") or "").strip()
    command = command_name(text)

    if command in {"/start", "/help"}:
        send_telegram_message(chat_id, HELP_TEXT)
        return

    if command in {"/digest", "/today"}:
        send_telegram_message(chat_id, "Собираю дайджест. Сейчас загляну в почту.")
        try:
            send_digest_to_chat(chat_id)
        except Exception as error:
            send_telegram_message(chat_id, f"Не получилось собрать дайджест: {error}")
        return

    if command == "/status":
        send_telegram_message(chat_id, format_status())
        return

    send_telegram_message(chat_id, "Я понимаю команды /digest, /status и /help.")


def poll_telegram_updates() -> int:
    env("TELEGRAM_BOT_TOKEN", required=True)
    env("TELEGRAM_CHAT_ID", required=True)
    offset: int | None = None
    last_daily_digest_date = initial_daily_digest_date()
    print(
        f"Mail Digest Bot is running. Send /digest in Telegram. Daily digest: {daily_digest_status()}.",
        flush=True,
    )
    try:
        set_telegram_commands()
    except Exception as error:
        print(f"could not set Telegram commands: {error}", file=sys.stderr, flush=True)

    while True:
        try:
            last_daily_digest_date = maybe_send_daily_digest(last_daily_digest_date)
            params = {
                "timeout": "30",
                "allowed_updates": json.dumps(["message", "callback_query"]),
            }
            if offset is not None:
                params["offset"] = str(offset)
            updates = telegram_request("getUpdates", params, timeout=35)
            if not isinstance(updates, list):
                continue
            for update in updates:
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                message = update.get("message")
                if isinstance(message, dict):
                    handle_telegram_message(message)
                callback_query = update.get("callback_query")
                if isinstance(callback_query, dict):
                    handle_callback_query(callback_query)
        except KeyboardInterrupt:
            print("Mail Digest Bot stopped.", flush=True)
            return 0
        except Exception as error:
            print(f"polling failed: {error}", file=sys.stderr, flush=True)
            time.sleep(5)


def run_once() -> int:
    try:
        items = fetch_messages()
        digest = format_digest(items)
        send_telegram(digest)
    except Exception as error:
        print(f"mail-digest-bot failed: {error}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    load_dotenv()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) > 1 and sys.argv[1] in {"--once", "once"}:
        return run_once()
    if len(sys.argv) > 1 and sys.argv[1] in {"--help", "-h"}:
        print("Usage: python mail_digest_bot.py [--once]\n\nБез аргументов запускает Telegram-бота.")
        return 0
    return poll_telegram_updates()


if __name__ == "__main__":
    raise SystemExit(main())
