"""Заявки на вступление в вуз.

Пользователь при первом входе выбирает один или несколько вузов, в каждом — профиль
(студент или преподаватель) и, для студента, группу, и указывает своё имя. Администратор
вуза с правом members.manage видит карточку заявки и одобряет или отклоняет её.
Одобрение создаёт членство (или добавляет профиль) и записывает студента в группу.
"""
from typing import Optional

from sqlalchemy.orm import Session

from auth.authorization import ActorContext
from database.tables import (
    Institution,
    InstitutionStatus,
    JoinRequest,
    JoinRequestStatus,
    Membership,
    StudyGroup,
    StudyGroupMember,
    User,
    utc_now,
)
from platform_core import registry
from platform_core.concurrency import is_uuid
from platform_core.errors import DomainError, not_found, validation
from services.institution_admin import Result, _body

JOIN_PROFILES = ("student", "teacher")
MAX_ITEMS = 10


def _name(inst: Optional[Institution]) -> str:
    return (inst.titles or {}).get("ru", "ВУЗ") if inst else "ВУЗ"


def request_view(r: JoinRequest, inst: Optional[Institution] = None) -> dict:
    return {
        "id": r.id, "institution_id": r.institution_id, "institution_name": _name(inst),
        "user_id": r.user_id, "full_name": r.full_name, "profile": r.profile,
        "group_id": r.group_id, "group_name": r.group_name, "status": r.status,
        "decision_reason": r.decision_reason, "created_at": registry.iso(r.created_at),
        "reviewed_at": registry.iso(r.reviewed_at),
    }


def _full_name(value) -> str:
    name = " ".join(value.split()) if isinstance(value, str) else ""
    if not name or len(name) > 200:
        raise validation("Укажите имя — от 1 до 200 символов", "full_name")
    return name


# --------------------------------------------------------------------------
# Пользователь
# --------------------------------------------------------------------------

def join_options(db: Session, user: User) -> Result:
    """Зарегистрированные активные вузы с их группами — единственный источник выбора в форме.
    profiles — уже имеющиеся у пользователя профили: в таком вузе можно попросить только недостающий."""
    member_of = {m.institution_id: list(m.profiles or []) for m in db.query(Membership).filter(Membership.user_id == user.id)}
    groups = {}
    for g in db.query(StudyGroup).order_by(StudyGroup.name_key):
        groups.setdefault(g.institution_id, []).append({"id": g.id, "name": g.name})
    items = [{"id": inst.id, "display_name": _name(inst), "groups": groups.get(inst.id, []),
              "profiles": member_of.get(inst.id, [])}
             for inst in db.query(Institution).filter(Institution.status == InstitutionStatus.ACTIVE.value)]
    items.sort(key=lambda i: i["display_name"].casefold())
    return Result({"items": items, "next_cursor": None})


def my_requests(db: Session, user: User) -> Result:
    rows = db.query(JoinRequest).filter(JoinRequest.user_id == user.id).order_by(JoinRequest.created_at.desc()).all()
    return Result({"items": [request_view(r, db.get(Institution, r.institution_id)) for r in rows], "next_cursor": None})


def submit(db: Session, user: User, payload) -> Result:
    """Создаёт заявки сразу в несколько вузов одной транзакцией: либо все, либо ни одной."""
    body = _body(payload, {"full_name", "items"}, {"full_name", "items"})
    full_name = _full_name(body["full_name"])
    items = body["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise validation(f"Выберите от 1 до {MAX_ITEMS} вузов", "items")
    seen, created = set(), []
    for i, item in enumerate(items):
        path = f"items[{i}]"
        item = _body(item, {"institution_id", "profile", "group_id"}, {"institution_id", "profile"})
        inst_id = item["institution_id"]
        if not is_uuid(inst_id):
            raise validation("institution_id должен быть UUID", f"{path}.institution_id")
        if inst_id in seen:
            raise validation("Вуз выбран дважды", f"{path}.institution_id")
        seen.add(inst_id)
        profile = item["profile"]
        if profile not in JOIN_PROFILES:
            raise validation("Профиль: student или teacher", f"{path}.profile")
        inst = db.get(Institution, inst_id)
        if not inst or inst.status != InstitutionStatus.ACTIVE.value:
            raise DomainError(404, "RESOURCE_NOT_FOUND", "Вуз не найден или не принимает заявки",
                              [{"path": f"{path}.institution_id", "message": "not found"}])
        membership = db.get(Membership, (inst_id, user.id))
        if membership and profile in (membership.profiles or []):
            raise DomainError(409, "MEMBERSHIP_ALREADY_EXISTS", f"Вы уже состоите в вузе «{_name(inst)}» с этим профилем",
                              [{"path": path, "message": "already member"}])
        if db.query(JoinRequest).filter(JoinRequest.institution_id == inst_id, JoinRequest.user_id == user.id,
                                        JoinRequest.status == JoinRequestStatus.PENDING.value).first():
            raise DomainError(409, "JOIN_REQUEST_PENDING", f"Заявка в вуз «{_name(inst)}» уже ждёт решения",
                              [{"path": path, "message": "pending"}])
        group = None
        if profile == "student":
            group_id = item.get("group_id")
            # Группа — только из зарегистрированных в вузе: свободного ввода нет.
            if group_id is None:
                raise validation("Выберите группу из списка", f"{path}.group_id")
            group = db.get(StudyGroup, group_id) if isinstance(group_id, str) and is_uuid(group_id) else None
            if not group or group.institution_id != inst_id:
                raise validation("Группа не найдена в этом вузе", f"{path}.group_id")
        elif item.get("group_id") is not None:
            raise validation("Группа указывается только студенту", f"{path}.group_id")
        row = JoinRequest(institution_id=inst_id, user_id=user.id, full_name=full_name, profile=profile,
                          group_id=group.id if group else None, group_name=group.name if group else None,
                          created_at=utc_now())
        db.add(row)
        db.flush()
        registry.audit(db, scope="institution", action="join_request.submit", actor_user_id=user.id,
                       actor_kind="user", institution_id=inst_id, target_type="join_request", target_id=row.id,
                       details={"profile": profile, "group_id": row.group_id})
        created.append((row, inst))
    db.commit()
    return Result({"items": [request_view(r, inst) for r, inst in created], "next_cursor": None}, status=201)


def withdraw(db: Session, user: User, request_id: str) -> Result:
    row = db.get(JoinRequest, request_id) if is_uuid(request_id) else None
    if not row or row.user_id != user.id:
        raise not_found("Заявка не найдена")
    if row.status != JoinRequestStatus.PENDING.value:
        raise DomainError(409, "INVALID_STATE", "Заявка уже рассмотрена")
    row.status = JoinRequestStatus.WITHDRAWN.value
    row.reviewed_at = utc_now()
    db.commit()
    return Result(request_view(row, db.get(Institution, row.institution_id)))


# --------------------------------------------------------------------------
# Администратор вуза (private API)
# --------------------------------------------------------------------------

def list_for_institution(db: Session, ctx: ActorContext, status: Optional[str]) -> Result:
    ctx.require("members.read")
    query = db.query(JoinRequest).filter(JoinRequest.institution_id == ctx.institution_id)
    if status is not None:
        if status not in {s.value for s in JoinRequestStatus}:
            raise validation("status: pending, approved, rejected или withdrawn", "status")
        query = query.filter(JoinRequest.status == status)
    inst = db.get(Institution, ctx.institution_id)
    rows = query.order_by(JoinRequest.created_at.desc()).limit(200).all()
    return Result({"items": [request_view(r, inst) for r in rows], "next_cursor": None})


def _pending(db: Session, ctx: ActorContext, request_id: str) -> JoinRequest:
    row = db.get(JoinRequest, request_id) if is_uuid(request_id) else None
    if not row or row.institution_id != ctx.institution_id:
        raise not_found("Заявка не найдена")
    if row.status != JoinRequestStatus.PENDING.value:
        raise DomainError(409, "INVALID_STATE", "Заявка уже рассмотрена")
    return row


def approve(db: Session, ctx: ActorContext, request_id: str) -> Result:
    ctx.require("members.manage")
    inst = registry.lock_institution(db, ctx.institution_id)
    row = _pending(db, ctx, request_id)
    membership = db.get(Membership, (ctx.institution_id, row.user_id))
    if membership:
        if row.profile not in (membership.profiles or []):
            membership.profiles = [*(membership.profiles or []), row.profile]
    else:
        membership = Membership(institution_id=ctx.institution_id, user_id=row.user_id, profiles=[row.profile],
                                created_at=utc_now())
        db.add(membership)
        db.flush()
    group_id = None
    if row.profile == "student" and row.group_id and not db.get(StudyGroupMember, (ctx.institution_id, row.user_id)):
        group = db.get(StudyGroup, row.group_id)
        if group and group.institution_id == ctx.institution_id:
            db.add(StudyGroupMember(institution_id=ctx.institution_id, user_id=row.user_id, group_id=group.id))
            group.members_revision += 1
            group_id = group.id
    row.status = JoinRequestStatus.APPROVED.value
    row.reviewed_by, row.reviewed_at = ctx.actor_id, utc_now()
    ctx.audit(db, "join_request.approve", "join_request", row.id,
              {"user_id": row.user_id, "profile": row.profile, "group_id": group_id})
    db.commit()
    return Result(request_view(row, inst))


def reject(db: Session, ctx: ActorContext, request_id: str, payload) -> Result:
    ctx.require("members.manage")
    body = _body(payload if payload is not None else {}, {"reason"})
    reason = body.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
        raise validation("Причина — строка до 500 символов", "reason")
    inst = registry.lock_institution(db, ctx.institution_id)
    row = _pending(db, ctx, request_id)
    row.status = JoinRequestStatus.REJECTED.value
    row.decision_reason = reason.strip() if reason and reason.strip() else None
    row.reviewed_by, row.reviewed_at = ctx.actor_id, utc_now()
    ctx.audit(db, "join_request.reject", "join_request", row.id, {"user_id": row.user_id})
    db.commit()
    return Result(request_view(row, inst))


def member_names(db: Session, institution_id: str) -> dict:
    """Имя участника из последней одобренной заявки — чтобы в админке были не только UUID."""
    rows = db.query(JoinRequest).filter(JoinRequest.institution_id == institution_id,
                                        JoinRequest.status == JoinRequestStatus.APPROVED.value) \
        .order_by(JoinRequest.reviewed_at.asc()).all()
    return {r.user_id: r.full_name for r in rows}
