"""Модуль пульта оператора «Тестовые данные»: вузы, группы, пользователи и расписание для демонстрации.

Весь модуль — эта папка (test-data/operator-demo). Пульт загружает его из OPERATOR_PLUGINS_DIR, куда папку
кладёт scripts/deploy.sh. Удалите папку — после следующего deploy.sh кнопок в пульте не будет, остальное
приложение от модуля не зависит.

Создание:
1. В базе ядра — вузы (с администрированием, «Людьми» и расписанием, как у настоящих), группы,
   преподаватели, студенты и администратор вуза; имена — из одобренных заявок на вступление.
2. В фоне — расписание на две недели: раннер поднимает сервис расписания вуза, сервис публикует роль
   «Редактор расписания»; её получает тестовый администратор, и от его сессии занятия загружаются
   импортом расписания (POST /api/v1/schedule/import) — тем же путём, что у людей.

Всё созданное записывается в test_data_records. Удаление стирает ровно эти вузы (как «Удалить вуз»:
данные сервисов в раннерах тоже) и этих пользователей. Реальные вузы и люди не затрагиваются.
"""
import hashlib
import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends
from sqlalchemy import Column, DateTime, String

from database.base import generate_uuid, table_class, utc_now
from database.create_tables import engine, session_local
from database.tables import (Institution, JoinRequest, Membership, RoleAssignment, ServiceInstance, ServiceRole,
                             StudyGroup, StudyGroupMember, User)
from platform_core import registry
import privacy
from platform_core.concurrency import is_uuid
from platform_core.errors import DomainError, validation

TITLE = "Тестовые данные"


class TestDataRecord(table_class):
    """Что создал модуль: удаляется ровно это и ничего больше. Таблица создаётся при загрузке модуля."""
    __tablename__ = "test_data_records"
    __table_args__ = {"extend_existing": True}

    kind = Column(String(20), primary_key=True)  # institution | user
    object_id = Column(String(36), primary_key=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

log = logging.getLogger("test_data")

SCHEDULE_RUNNER = os.environ.get("RUNNER_SCHEDULE_URL", "http://schedule:8000").rstrip("/")
ROLE_WAIT_SECONDS = 180

UNIVERSITIES = ["Тестовый технический университет", "Тестовый педагогический университет",
                "Тестовый университет связи", "Тестовый экономический университет", "Тестовый медицинский университет",
                "Тестовый аграрный университет", "Тестовый университет искусств", "Тестовый физико-математический институт",
                "Тестовый горный университет", "Тестовый юридический институт"]
GROUP_PREFIXES = ["ИВТ", "ПИ", "БИ", "ИБ", "ЭК", "ФИ", "МТ", "РТ"]
FIRST_M = ["Иван", "Пётр", "Алексей", "Дмитрий", "Сергей", "Андрей", "Максим", "Никита", "Артём", "Егор", "Кирилл", "Роман"]
FIRST_F = ["Анна", "Мария", "Елена", "Ольга", "Дарья", "Полина", "Софья", "Алина", "Виктория", "Ксения", "Екатерина", "Юлия"]
LAST = ["Иванов", "Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов", "Новиков", "Фёдоров",
        "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов", "Егоров", "Павлов", "Козлов", "Степанов", "Николаев"]
PATRONYMIC_M = ["Иванович", "Петрович", "Сергеевич", "Андреевич", "Олегович", "Викторович"]
PATRONYMIC_F = ["Ивановна", "Петровна", "Сергеевна", "Андреевна", "Олеговна", "Викторовна"]
SUBJECTS = ["Математический анализ", "Линейная алгебра", "Программирование", "Базы данных", "Физика", "Английский язык",
            "Операционные системы", "Компьютерные сети", "Дискретная математика", "История", "Экономика",
            "Теория вероятностей", "Информационная безопасность", "Философия"]
PAIRS = [("08:30", "10:05"), ("10:15", "11:50"), ("12:00", "13:35"), ("14:00", "15:35"), ("15:45", "17:20")]

_status = {"state": "idle", "message": "Тестовых данных нет", "progress": ""}
_lock = threading.Lock()


def status() -> dict:
    with _lock:
        state = dict(_status)
    with session_local() as db:
        state["institutions"] = db.query(TestDataRecord).filter_by(kind="institution").count()
        state["users"] = db.query(TestDataRecord).filter_by(kind="user").count()
    if state["state"] == "idle":
        state["message"] = "Тестовых данных нет" if not state["institutions"] else "Тестовые данные созданы"
    return state


def _set(**values):
    with _lock:
        _status.update(values)


def _person(rng: random.Random) -> str:
    if rng.random() < .5:
        return f"{rng.choice(LAST)} {rng.choice(FIRST_M)} {rng.choice(PATRONYMIC_M)}"
    last = rng.choice(LAST)
    last = last[:-1] + "ая" if last.endswith("ий") else last + "а"
    return f"{last} {rng.choice(FIRST_F)} {rng.choice(PATRONYMIC_F)}"


def _user(db, name: str, institution_id: str, profile: str, rng: random.Random, group: Optional[StudyGroup] = None) -> str:
    """Тестовый пользователь (войти через MAX не может: MAX ID вымышленный) и одобренная заявка с именем."""
    user = User(id=generate_uuid(), max_user_id=str(9_000_000_000 + rng.randrange(10**9)), username=f"test-{generate_uuid()[:12]}")
    db.add(user)
    db.flush()
    db.add(TestDataRecord(kind="user", object_id=user.id))
    # Ядро пускает в сервисы только давших согласие; вымышленный человек получает его сразу.
    db.add(privacy.Consent(user_id=user.id, version=privacy.VERSION, document=privacy.TEXT,
                           document_hash=hashlib.sha256(privacy.TEXT.encode()).hexdigest()))
    db.add(JoinRequest(institution_id=institution_id, user_id=user.id, full_name=name,
                       profile="student" if profile == "student" else "teacher", group_id=group.id if group else None,
                       group_name=group.name if group else None, status="approved", reviewed_at=utc_now()))
    return user.id


def create(params, operator_user: Optional[str]) -> dict:
    """Создаёт вузы, группы и людей в ядре (одна транзакция) и запускает загрузку расписания в фоне."""
    if not isinstance(params, dict):
        params = {}
    try:
        count = int(params.get("institutions", 3))
        per_group = int(params.get("students", 15))
    except (TypeError, ValueError):
        raise validation("Число вузов и студентов — целые числа", "institutions")
    if not 1 <= count <= 10 or not 3 <= per_group <= 40:
        raise validation("Вузов — от 1 до 10, студентов в группе — от 3 до 40", "institutions")
    with _lock:
        if _status["state"] == "running":
            raise DomainError(409, "INVALID_STATE", "Тестовые данные уже создаются — дождитесь окончания")
        _status.update(state="running", message="Создаём вузы и людей…", progress="")
    plans = []
    try:
        with session_local() as db:
            if operator_user and not db.get(User, operator_user):
                raise DomainError(404, "RESOURCE_NOT_FOUND", "Пользователь не найден: он должен войти через MAX")
            existing = {i.titles.get("ru") for i in db.query(Institution)}
            names = [n for n in UNIVERSITIES if n not in existing] + [f"Тестовый университет №{i}" for i in range(1, 100)]
            for n in range(count):
                rng = random.Random()
                inst = registry.provision_institution(db, {"ru": names[n]}, "ru")
                db.add(TestDataRecord(kind="institution", object_id=inst.id))
                groups = []
                for prefix in rng.sample(GROUP_PREFIXES, 4):
                    group = StudyGroup(institution_id=inst.id, name=f"{prefix}-{rng.randint(21, 25)}{rng.randint(1, 3)}")
                    group.name_key = group.name.casefold()
                    db.add(group)
                    groups.append(group)
                db.flush()
                teachers = []
                for _ in range(6):
                    uid = _user(db, _person(rng), inst.id, "teacher", rng)
                    db.add(Membership(institution_id=inst.id, user_id=uid, profiles=["teacher"]))
                    teachers.append(uid)
                for group in groups:
                    for _ in range(per_group):
                        uid = _user(db, _person(rng), inst.id, "student", rng, group)
                        db.add(Membership(institution_id=inst.id, user_id=uid, profiles=["student"]))
                        db.add(StudyGroupMember(institution_id=inst.id, user_id=uid, group_id=group.id))
                admin = _user(db, _person(rng), inst.id, "teacher", rng)
                db.flush()
                registry.assign_owner(db, inst.id, admin)
                if operator_user:
                    # Реальный пользователь получает все профили, чтобы посмотреть вуз глазами каждого.
                    registry.assign_owner(db, inst.id, operator_user)
                    member = db.get(Membership, (inst.id, operator_user))
                    member.profiles = ["student", "teacher", "admin"]
                    db.add(StudyGroupMember(institution_id=inst.id, user_id=operator_user, group_id=groups[0].id))
                schedule = db.query(ServiceInstance).filter_by(institution_id=inst.id, service_type="schedule",
                                                               deleted_at=None).one()
                plans.append({"institution_id": inst.id, "title": names[n], "schedule_id": schedule.id, "admin": admin,
                              "operator_user": operator_user, "rows": _lessons(rng, groups, teachers)})
            registry.audit(db, scope="platform", action="test_data.create", actor_user_id=None, actor_kind="operator",
                           target_type="test_data", target_id=None,
                           details={"institutions": count, "students_per_group": per_group})
            db.commit()
    except Exception as error:
        _set(state="error", message=getattr(error, "message", None) or f"Не удалось создать: {error}")
        raise
    threading.Thread(target=_load_schedules, args=(plans,), name="test-data", daemon=True).start()
    return {"message": f"Создано вузов: {count}. Расписание загружается в фоне — статус обновится сам."}


def _lessons(rng: random.Random, groups, teachers) -> list:
    """Занятия на текущую и следующую неделю, пн–пт, 2–4 пары в день у каждой группы."""
    monday = date.today() - timedelta(days=date.today().weekday())
    rows = []
    for group in groups:
        for day in range(12):
            d = monday + timedelta(days=day)
            if d.weekday() >= 5:
                continue
            for start, end in sorted(rng.sample(PAIRS, rng.randint(2, 4))):
                rows.append({"Дата": d.strftime("%d.%m.%Y"), "Начало": start, "Конец": end, "Дисциплина": rng.choice(SUBJECTS),
                             "Группы": group.name, "Преподаватели": rng.choice(teachers),
                             "Аудитория": f"{rng.choice('АБВ')}-{rng.randint(101, 420)}",
                             "Статус": "отменено" if rng.random() < .04 else "",
                             "Тип": rng.choice(("лекция", "семинар", "лабораторная"))})
    return rows


def _service_token(plan: dict, user_id: str) -> str:
    """Сессия пользователя в расписании вуза — как её выдаёт ядро при входе в сервис."""
    from auth.models import CoreSession, ServiceSession
    from auth.service import pair
    with session_local() as db:
        expires = utc_now() + timedelta(hours=1)
        parent = CoreSession(id=generate_uuid(), user_id=user_id, family_id=generate_uuid(),
                             current_refresh_jti=generate_uuid(), expires_at=expires)
        db.add(parent)
        db.flush()
        session = ServiceSession(id=generate_uuid(), parent_session_id=parent.id, user_id=user_id,
                                 institution_id=plan["institution_id"], service_id=plan["schedule_id"], profile="admin",
                                 family_id=generate_uuid(), current_refresh_jti=generate_uuid(), expires_at=expires)
        db.add(session)
        db.flush()
        token = pair(db, session, parent)["access_token"]
        db.commit()
        return token


def _load_schedules(plans: list) -> None:
    done, failed = 0, []
    for plan in plans:
        _set(message=f"Загружаем расписание: {plan['title']}", progress=f"{done} из {len(plans)}")
        try:
            _load_one(plan)
            done += 1
        except Exception as error:  # noqa: BLE001 — показать в пульте, продолжить с остальными
            log.exception("test schedule for %s failed", plan["institution_id"])
            failed.append(f"{plan['title']}: {error}")
    if failed:
        _set(state="error", message="Вузы и люди созданы, но расписание загрузилось не везде: " + "; ".join(failed), progress="")
    else:
        _set(state="idle", message="Тестовые данные созданы", progress="")


def _load_one(plan: dict) -> None:
    # 1. Сервис расписания вуза запущен раннером и опубликовал роль «Редактор расписания».
    deadline = time.monotonic() + ROLE_WAIT_SECONDS
    while True:
        with session_local() as db:
            if db.get(ServiceRole, (plan["schedule_id"], "schedule_editor")):
                for uid in filter(None, (plan["admin"], plan["operator_user"])):
                    row = db.get(RoleAssignment, (plan["schedule_id"], uid, "admin"))
                    if row is None:
                        db.add(RoleAssignment(service_id=plan["schedule_id"], user_id=uid, profile="admin",
                                              roles=["schedule_editor"]))
                    elif "schedule_editor" not in (row.roles or []):
                        row.roles = [*(row.roles or []), "schedule_editor"]
                db.commit()
                break
        if time.monotonic() > deadline:
            raise RuntimeError("сервис расписания не запустился за 3 минуты")
        time.sleep(3)
    # 2. Импорт занятий от имени тестового администратора (повторяем, пока раннер не узнал экземпляр).
    token = _service_token(plan, plan["admin"])
    rows = plan["rows"]
    for start in range(0, len(rows), 1500):
        body = json.dumps(rows[start:start + 1500], ensure_ascii=False).encode()
        url = f"{SCHEDULE_RUNNER}/{plan['schedule_id']}/api/v1/schedule/import?name=test.json&dry_run=false"
        while True:
            request = urllib.request.Request(url, data=body, method="POST", headers={
                "Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"})
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    json.loads(response.read())
                break
            except urllib.error.HTTPError as error:
                if error.code in (404, 503) and time.monotonic() < deadline:
                    time.sleep(3)
                    continue
                raise RuntimeError(f"импорт расписания: HTTP {error.code} {error.read()[:200]!r}")
            except urllib.error.URLError as error:
                if time.monotonic() < deadline:
                    time.sleep(3)
                    continue
                raise RuntimeError(f"раннер расписания недоступен: {error.reason}")


def delete(staff) -> dict:
    """Удаляет только записанное в test_data_records: вузы (с данными сервисов в раннерах) и тестовых людей."""
    from services import platform_ops, platform_support
    with _lock:
        if _status["state"] == "running":
            raise DomainError(409, "INVALID_STATE", "Тестовые данные ещё создаются — дождитесь окончания")
    removed_inst = removed_users = 0
    with session_local() as db:
        for record in db.query(TestDataRecord).filter_by(kind="institution").all():
            inst = db.get(Institution, record.object_id)
            if inst:
                platform_support.delete_institution(db, staff, inst.id, {"confirm_title": inst.titles.get("ru", "")})
                removed_inst += 1
            db.query(TestDataRecord).filter_by(kind="institution", object_id=record.object_id).delete()
            db.commit()
        for record in db.query(TestDataRecord).filter_by(kind="user").all():
            user = db.get(User, record.object_id)
            if user and not db.query(Membership).filter_by(user_id=user.id).first():
                platform_ops.delete_user(db, user.id)
                # Согласие настоящих людей переживает удаление (доказательство); у вымышленных — нет.
                consent = db.get(privacy.Consent, user.id)
                if consent:
                    db.delete(consent)
                removed_users += 1
            if not user or not db.query(Membership).filter_by(user_id=record.object_id).first():
                db.query(TestDataRecord).filter_by(kind="user", object_id=record.object_id).delete()
        registry.audit(db, scope="platform", action="test_data.delete", actor_user_id=None, actor_kind="operator",
                       target_type="test_data", target_id=None, details={"institutions": removed_inst, "users": removed_users})
        db.commit()
    _set(state="idle", message="Тестовых данных нет", progress="")
    return {"message": f"Удалено тестовых вузов: {removed_inst}, пользователей: {removed_users}."}


# --------------------------------------------------------------------------
# Подключение к пульту: register(app, ctx) вызывает загрузчик модулей пульта
# --------------------------------------------------------------------------

def register(app, ctx) -> None:
    TestDataRecord.__table__.create(bind=engine, checkfirst=True)
    router = APIRouter(prefix=f"/api/plugins/{ctx['plugin_id']}")
    operator = ctx["operator"]

    @router.get("/status")
    def get_status(_: str = Depends(operator)):
        return status()

    @router.post("/create")
    def post_create(payload: Any = Body(None), _: str = Depends(operator)):
        user_id = payload.get("user_id") if isinstance(payload, dict) else None
        if user_id is not None and not is_uuid(user_id):
            raise validation("Выберите пользователя из списка", "user_id")
        return create(payload, user_id)

    @router.post("/delete")
    def post_delete(_: str = Depends(operator)):
        return delete(ctx["staff"])

    app.include_router(router)
