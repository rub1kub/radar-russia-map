"""Кто из принёсших сообщение засчитан в подтверждение.

Число под заголовком события и список источников под ним считались в разных
местах и по-разному: в шапке стояло 20, в списке набиралось 16, и проверить
карту было нельзя — а именно ради проверяемости список и раскрывается.

Здесь правило одно и лежит в одном месте. Не засчитываются:

  * повтор того же канала — он уже сказал своё;
  * дословный перепост чужого текста — пересказ не свидетельство;
  * канал той же сети, что и уже засчитанный, — у оператора один голос,
    сколько бы веток он ни завёл.

Право первым сказать текст и выступить от сети закрепляется по всем строкам,
включая повторы: иначе первым окажется другой канал и пометки разойдутся.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fuse import Fuser
from .parse import strip_footer


@dataclass(frozen=True)
class Contribution:
    source_key: str
    role: str
    at: str
    text: str
    # Ссылка на само сообщение в Telegram. Без неё «19 источников» —
    # число, которое остаётся принимать на веру: ники в списке не
    # кликаются, и проверить их нельзя.
    link: str | None
    first_from_source: bool
    repost: bool
    clone: bool
    counted: bool


def message_link(username: str | None, message_id) -> str | None:
    """Постоянная ссылка на сообщение публичного канала."""
    if not username or not message_id:
        return None
    return f"https://t.me/{username}/{message_id}"


def walk(
    rows,
    networks: dict[str, str | None],
    usernames: dict[str, str] | None = None,
) -> list[Contribution]:
    """Разметить вклады события. rows — по возрастанию времени."""
    usernames = usernames or {}
    seen: set[str] = set()
    said_first: dict[str, str] = {}
    network_first: dict[str, str] = {}
    out: list[Contribution] = []

    for row in rows:
        source_key = row["source_key"]
        stamp = Fuser._repost_key(strip_footer(row["text"] or ""))
        if stamp is not None:
            said_first.setdefault(stamp, source_key)
        network = networks.get(source_key)
        if network:
            network_first.setdefault(network, source_key)

        if row["role"] == "repeat":
            continue

        first_from_source = source_key not in seen
        seen.add(source_key)
        repost = stamp is not None and said_first[stamp] != source_key
        clone = bool(network and network_first[network] != source_key)
        out.append(Contribution(
            source_key=source_key,
            role=row["role"],
            at=row["contributed_at"],
            link=message_link(usernames.get(source_key), row.get("message_id")),
            text=" ".join((row["text"] or "").split())[:220],
            first_from_source=first_from_source,
            repost=repost,
            clone=clone,
            counted=first_from_source and not repost and not clone,
        ))
    return out


def counted(rows, networks: dict[str, str | None]) -> int:
    """Сколько независимых голосов стоит за событием."""
    return sum(1 for item in walk(rows, networks) if item.counted)
