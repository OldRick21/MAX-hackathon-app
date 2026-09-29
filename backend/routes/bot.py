"""Внутренний read-only API для MAX-бота.

Маршрут не публикуется Nginx и защищён отдельным BOT_CORE_TOKEN. Бот передаёт
только MAX user id; ядро само определяет актуальные членства и учебные группы.
"""
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from auth.dependencies import json_body
from database.create_tables import get_db
from database.tables import Institution, Membership, StudyGroup, StudyGroupChat, StudyGroupMember, User
from platform_core.concurrency import constant_time_token_match, split_bearer
from platform_core.errors import DomainError, validation
from settings.config import settings

router = APIRouter(prefix="/api/v1/internal/bot", tags=["Internal MAX bot"])
MAX_USER_ID_RE = re.compile(r"^[1-9][0-9]{0,31}$")


def require_bot(authorization: Optional[str] = Header(None, alias="Authorization")) -> None:
    scheme, token = split_bearer(authorization)
    if scheme != "bearer" or not settings.BOT_CORE_TOKEN or not constant_time_token_match(token, settings.BOT_CORE_TOKEN):
        raise DomainError(401, "UNAUTHENTICATED", "Недействительный токен бота",
                          headers={"WWW-Authenticate": "Bearer"})


def _body(payload: Any) -> str:
    if not isinstance(payload, dict) or set(payload) != {"max_user_id"}:
        raise validation("Тело должно содержать только max_user_id", "max_user_id")
    value = payload["max_user_id"]
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not MAX_USER_ID_RE.fullmatch(value):
        raise validation("max_user_id должен быть положительным целым идентификатором", "max_user_id")
    return value


@router.post("/group-chats/resolve", dependencies=[Depends(require_bot)])
def resolve_group_chats(payload: Any = Depends(json_body), db: Session = Depends(get_db)):
    max_user_id = _body(payload)
    user = db.query(User).filter(User.max_user_id == max_user_id).first()
    if not user:
        return {"institutions": []}

    rows = (
        db.query(Institution, StudyGroup, StudyGroupChat, Membership)
        .join(Membership, (Membership.institution_id == Institution.id) & (Membership.user_id == user.id))
        .join(StudyGroupMember, (StudyGroupMember.institution_id == Institution.id)
              & (StudyGroupMember.user_id == user.id))
        .join(StudyGroup, StudyGroup.id == StudyGroupMember.group_id)
        .join(StudyGroupChat, StudyGroupChat.group_id == StudyGroup.id)
        .filter(Institution.status == "active", StudyGroupChat.invite_url.isnot(None))
        .all()
    )
    institutions = {}
    for institution, group, chat, membership in rows:
        if "student" not in (membership.profiles or []):
            continue
        item = institutions.setdefault(institution.id, {
            "id": institution.id,
            "name": (institution.titles or {}).get(institution.default_locale)
                    or (institution.titles or {}).get("ru") or institution.id,
            "groups": [],
        })
        item["groups"].append({"id": group.id, "name": group.name, "chat_url": chat.invite_url})
    result = sorted(institutions.values(), key=lambda i: (i["name"].casefold(), i["id"]))
    for institution in result:
        institution["groups"].sort(key=lambda g: (g["name"].casefold(), g["id"]))
    return {"institutions": result}
