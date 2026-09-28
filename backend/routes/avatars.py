"""Аватары пользователей: свой загружает сам пользователь, чужой видят участники общих вузов.

Картинка одна на человека во всех вузах и сервисах. Фото из MAX не используется.
Тело PUT — сами байты изображения (Content-Type: image/jpeg, image/png или image/webp);
клиент заранее уменьшает картинку до квадрата.
"""
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from auth.dependencies import get_current_core_session
from database.create_tables import get_db
from database.tables import Membership, UserAvatar, utc_now
from platform_core.concurrency import is_uuid
from platform_core.errors import DomainError, not_found, validation

router = APIRouter(tags=["Avatars"])

MAX_BYTES = 512 * 1024
SIGNATURES = {
    "image/jpeg": lambda b: b[:3] == b"\xff\xd8\xff",
    "image/png": lambda b: b[:8] == b"\x89PNG\r\n\x1a\n",
    "image/webp": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
}


def _etag(avatar: UserAvatar) -> str:
    return f'"a{avatar.version}"'


def _can_see(db: Session, viewer_id: str, target_id: str) -> bool:
    if viewer_id == target_id:
        return True
    mine = {m.institution_id for m in db.query(Membership).filter(Membership.user_id == viewer_id)}
    return bool(mine) and db.query(Membership).filter(Membership.user_id == target_id,
                                                      Membership.institution_id.in_(mine)).first() is not None


@router.put("/api/v1/users/me/avatar")
async def upload_avatar(request: Request, session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    user, _ = session_data
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in SIGNATURES:
        raise validation("Формат картинки: JPEG, PNG или WebP", "content-type")
    data = await request.body()
    if not data:
        raise validation("Пустой файл", "body")
    if len(data) > MAX_BYTES:
        raise DomainError(413, "PAYLOAD_TOO_LARGE", "Картинка больше 512 КБ")
    if not SIGNATURES[content_type](data):
        raise validation("Файл не похож на картинку этого формата", "body")
    avatar = db.get(UserAvatar, user.id)
    if avatar:
        avatar.content_type, avatar.data, avatar.version, avatar.updated_at = content_type, data, avatar.version + 1, utc_now()
    else:
        avatar = UserAvatar(user_id=user.id, content_type=content_type, data=data, version=1, updated_at=utc_now())
        db.add(avatar)
    db.commit()
    return {"user_id": user.id, "version": avatar.version}


@router.delete("/api/v1/users/me/avatar", status_code=204)
def delete_avatar(session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    user, _ = session_data
    avatar = db.get(UserAvatar, user.id)
    if avatar:
        db.delete(avatar)
        db.commit()
    return Response(status_code=204)


@router.get("/api/v1/users/{user_id}/avatar")
def get_avatar(user_id: str, request: Request, session_data=Depends(get_current_core_session),
               db: Session = Depends(get_db)):
    viewer, _ = session_data
    if user_id == "me":
        user_id = viewer.id
    # Чужой аватар и отсутствие аватара неразличимы: 404 в обоих случаях.
    avatar = db.get(UserAvatar, user_id) if is_uuid(user_id) else None
    if not avatar or not _can_see(db, viewer.id, user_id):
        raise not_found("Аватара нет")
    headers = {"ETag": _etag(avatar), "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == _etag(avatar):
        return Response(status_code=304, headers=headers)
    return Response(avatar.data, media_type=avatar.content_type, headers=headers)
