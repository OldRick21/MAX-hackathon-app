from typing import List
from platform_core import catalog, registry
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from database.tables import Membership, ServiceInstance, ServiceRole, RoleAssignment


class ServiceRegistry:
    @staticmethod
    def _verify_service_ownership(service_id: str, machine_claims: dict):
        token_service_id = getattr(machine_claims, "service_id", None) or machine_claims.get("service_id")
        if token_service_id != service_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Cannot access other service instance"
            )


    @staticmethod
    def get_service_manifest(service_id: str, machine_claims: dict, db: Session):
        ServiceRegistry._verify_service_ownership(service_id, machine_claims)
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")
        return service.manifest or {"titles": {"ru": service.service_type}, "menus": []}


    @staticmethod
    def replace_service_manifest(service_id: str, manifest_data, machine_claims: dict, db: Session):
        ServiceRegistry._verify_service_ownership(service_id, machine_claims)
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        service.manifest = manifest_data.model_dump()
        db.commit()
        return service.manifest


    @staticmethod
    def get_service_user_profiles(service_id: str, user_id: str, machine_claims: dict, db: Session):
        ServiceRegistry._verify_service_ownership(service_id, machine_claims)
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        membership = db.query(Membership).filter(
            Membership.institution_id == service.institution_id,
            Membership.user_id == user_id
        ).first()

        if not membership:
            raise HTTPException(status_code=404, detail="User is not a member of this institution")

        return {
            "user_id": user_id,
            "institution_id": service.institution_id,
            "profiles": membership.profiles or []
        }


    @staticmethod
    def list_service_roles(service_id: str, machine_claims: dict, db: Session):
        ServiceRegistry._verify_service_ownership(service_id, machine_claims)
        roles = db.query(ServiceRole).filter(ServiceRole.service_id == service_id).all()
        items = [
            {
                "service_id": r.service_id,
                "code": r.code,
                "titles": r.titles,
                "allowed_profiles": r.allowed_profiles or [],
                "permissions": r.permissions or [],
                "system": r.system
            }
            for r in roles
        ]
        return {"items": items, "next_cursor": None}


    @staticmethod
    def create_service_role(service_id: str, role_data, machine_claims: dict, db: Session):
        ServiceRegistry._verify_service_ownership(service_id, machine_claims)
        registry.lock_institution(db, machine_claims['institution_id'])
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        if service.protected:
            raise HTTPException(status_code=403, detail="PROTECTED_RESOURCE: Administration role definitions are protected")

        existing = db.query(ServiceRole).filter(
            ServiceRole.service_id == service_id,
            ServiceRole.code == role_data.code
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Role '{role_data.code}' already exists")

        catalog.check_role_input(role_data.model_dump(exclude_none=True), service.service_type, service.supported_profiles)
        new_role = ServiceRole(
            service_id=service_id,
            code=role_data.code,
            titles=role_data.titles.model_dump(),
            allowed_profiles=role_data.allowed_profiles,
            permissions=role_data.permissions,
            system=False
        )
        db.add(new_role)
        db.commit()

        return {
            "service_id": new_role.service_id,
            "code": new_role.code,
            "titles": new_role.titles,
            "allowed_profiles": new_role.allowed_profiles,
            "permissions": new_role.permissions,
            "system": new_role.system
        }


    @staticmethod
    def get_service_role(service_id: str, role_code: str, machine_claims: dict, db: Session):
        ServiceRegistry._verify_service_ownership(service_id, machine_claims)
        role = db.query(ServiceRole).filter(
            ServiceRole.service_id == service_id,
            ServiceRole.code == role_code
        ).first()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        return {
            "service_id": role.service_id,
            "code": role.code,
            "titles": role.titles,
            "allowed_profiles": role.allowed_profiles,
            "permissions": role.permissions,
            "system": role.system
        }


    @staticmethod
    def update_service_role(service_id: str, role_code: str, patch_data, machine_claims: dict, db: Session):
        ServiceRegistry._verify_service_ownership(service_id, machine_claims)
        registry.lock_institution(db, machine_claims['institution_id'])
        role = db.query(ServiceRole).filter(
            ServiceRole.service_id == service_id,
            ServiceRole.code == role_code
        ).first()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        if role.system or (role.service and role.service.protected):
            raise HTTPException(status_code=403, detail="PROTECTED_RESOURCE: System roles cannot be modified")

        catalog.check_role_patch(patch_data.model_dump(exclude_unset=True, exclude_none=True),
                                 role.service.service_type, role.service.supported_profiles)
        if patch_data.titles is not None:
            role.titles = patch_data.titles.model_dump()
        if patch_data.allowed_profiles is not None:
            role.allowed_profiles = patch_data.allowed_profiles
        if patch_data.permissions is not None:
            role.permissions = patch_data.permissions

        db.commit()
        return {
            "service_id": role.service_id,
            "code": role.code,
            "titles": role.titles,
            "allowed_profiles": role.allowed_profiles,
            "permissions": role.permissions,
            "system": role.system
        }


    @staticmethod
    def delete_service_role(service_id: str, role_code: str, machine_claims: dict, db: Session):
        ServiceRegistry._verify_service_ownership(service_id, machine_claims)
        registry.lock_institution(db, machine_claims['institution_id'])
        role = db.query(ServiceRole).filter(
            ServiceRole.service_id == service_id,
            ServiceRole.code == role_code
        ).first()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        if role.system or (role.service and role.service.protected):
            raise HTTPException(status_code=403, detail="PROTECTED_RESOURCE: System roles cannot be deleted")

        # Проверка: есть ли пользователи с этой ролью (ROLE_IN_USE -> 409)
        all_assignments = db.query(RoleAssignment).filter(RoleAssignment.service_id == service_id).all()
        for a in all_assignments:
            if role_code in (a.roles or []):
                raise HTTPException(status_code=409, detail="Role is currently assigned to users (ROLE_IN_USE)")

        db.delete(role)
        db.commit()


    @staticmethod
    def get_service_assignments(service_id: str, user_id: str, profile: str, machine_claims: dict, db: Session):
        ServiceRegistry._verify_service_ownership(service_id, machine_claims)
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        assignment = db.query(RoleAssignment).filter(
            RoleAssignment.service_id == service_id,
            RoleAssignment.user_id == user_id,
            RoleAssignment.profile == profile.lower()
        ).first()

        assigned_roles = assignment.roles if assignment else []
        permissions: List[str] = []
        if assigned_roles:
            roles_in_db = db.query(ServiceRole).filter(
                ServiceRole.service_id == service_id,
                ServiceRole.code.in_(assigned_roles)
            ).all()
            for r in roles_in_db:
                permissions.extend(r.permissions or [])

        return {
            "institution_id": service.institution_id,
            "service_id": service_id,
            "user_id": user_id,
            "profile": profile.lower(),
            "roles": assigned_roles,
            "permissions": list(set(permissions))
        }


    @staticmethod
    def replace_service_assignments(
        service_id: str,
        user_id: str,
        profile: str,
        assignment_data,
        machine_claims: dict,
        db: Session
    ):
        ServiceRegistry._verify_service_ownership(service_id, machine_claims)
        registry.lock_institution(db, machine_claims['institution_id'])
        profile_norm = profile.lower()
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")
        if service.protected:
            # Назначения administration меняет только owner через private API.
            raise HTTPException(status_code=403, detail="PROTECTED_RESOURCE: Administration assignments are protected")

        # 1. Проверяем, что пользователь состоит в этом ВУЗе и имеет данный профиль
        membership = db.query(Membership).filter(
            Membership.institution_id == service.institution_id,
            Membership.user_id == user_id
        ).first()
        if not membership or profile_norm not in [p.lower() for p in (membership.profiles or [])]:
            raise HTTPException(status_code=404, detail="Target user or profile does not exist in institution")

        # 2. Валидируем роли
        target_roles = catalog.check_role_codes(assignment_data.roles)
        if target_roles:
            existing_roles = db.query(ServiceRole).filter(
                ServiceRole.service_id == service_id,
                ServiceRole.code.in_(target_roles)
            ).all()
            existing_codes = {r.code: r for r in existing_roles}

            for role_code in target_roles:
                if role_code not in existing_codes:
                    raise HTTPException(status_code=400, detail=f"Role '{role_code}' does not exist in this service")
                # Проверяем, разрешен ли профиль пользователя для этой роли
                role_obj = existing_codes[role_code]
                if profile_norm not in [p.lower() for p in (role_obj.allowed_profiles or [])]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Role '{role_code}' is not allowed for profile '{profile_norm}'"
                    )

        # 3. Сохраняем назначения
        assignment = db.query(RoleAssignment).filter(
            RoleAssignment.service_id == service_id,
            RoleAssignment.user_id == user_id,
            RoleAssignment.profile == profile_norm
        ).first()

        if not assignment:
            assignment = RoleAssignment(
                service_id=service_id,
                user_id=user_id,
                profile=profile_norm,
                roles=target_roles
            )
            db.add(assignment)
        else:
            assignment.roles = target_roles

        db.commit()

        # 4. Вычисляем итоговые права
        permissions: List[str] = []
        if target_roles:
            roles_in_db = db.query(ServiceRole).filter(
                ServiceRole.service_id == service_id,
                ServiceRole.code.in_(target_roles)
            ).all()
            for r in roles_in_db:
                permissions.extend(r.permissions or [])

        return {
            "institution_id": service.institution_id,
            "service_id": service_id,
            "user_id": user_id,
            "profile": profile_norm,
            "roles": target_roles,
            "permissions": list(set(permissions))
        }


