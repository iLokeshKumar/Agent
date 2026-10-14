"""Database-backed role definitions and section/action grants."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import (
    DEFAULT_ROLE_SECTIONS,
    DEFAULT_DEMO_SCENARIOS,
    PERMISSION_SECTIONS,
)
from app.models.access_control import RoleDefinition, RolePermission

ROLE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,49}$")
VALID_ACTIONS = {"view", "edit", "run"}
LOCKED_ROLES = {"admin", "system"}


def validate_role_name(name: str) -> str:
    value = name.strip().lower()
    if not ROLE_NAME_PATTERN.fullmatch(value):
        raise ValueError("Role names must be 2–50 lowercase letters, numbers, '_' or '-'.")
    return value


def list_roles(db: Session) -> list[str]:
    return list(db.execute(
        select(RoleDefinition.name).order_by(RoleDefinition.name)
    ).scalars())


def seed_role_access(db: Session) -> None:
    defaults = {
        **DEFAULT_ROLE_SECTIONS,
        "system": set(),
    }
    for role, sections in defaults.items():
        definition = db.get(RoleDefinition, role)
        if definition is None:
            definition = RoleDefinition(name=role, built_in=True)
            db.add(definition)
            db.flush()
            grants = [
                RolePermission(role=role, section=section, action="view")
                for section in sections
            ]
            grants.extend(
                RolePermission(role=role, section=section, action="edit")
                for section in _default_edit_sections(role, sections)
            )
            grants.extend(
                RolePermission(role=role, section=f"demo:{scenario}", action="run")
                for scenario in DEFAULT_DEMO_SCENARIOS.get(role, set())
            )
            db.add_all(grants)
    db.commit()


def _default_edit_sections(role: str, sections: set[str]) -> set[str]:
    if role == "admin":
        return set(sections)
    editable = {
        "finance": {"tasks", "approvals", "tally", "mail", "integrations"},
        "gsm": {"campaigns", "templates", "integrations"},
        "purchase": {"tasks", "approvals", "integrations"},
        "ops": {"tasks", "integrations"},
    }
    return editable.get(role, set()) & sections


def get_role_grants(db: Session, role: str) -> frozenset[tuple[str, str]]:
    return frozenset(db.execute(
        select(RolePermission.section, RolePermission.action)
        .where(RolePermission.role == role)
    ).all())


def set_role_grants(
    db: Session,
    role: str,
    grants: set[tuple[str, str]],
) -> None:
    if role in LOCKED_ROLES:
        raise ValueError(f"'{role}' is a protected built-in role and cannot be edited.")
    if db.get(RoleDefinition, role) is None:
        raise LookupError("Role not found.")
    for section, action in grants:
        if section not in PERMISSION_SECTIONS or action not in VALID_ACTIONS:
            raise ValueError(f"Invalid permission: {section}:{action}.")
        if section == "demo" and action != "view":
            raise ValueError("Live scenarios support View and individual scenario Run grants.")
        if section.startswith("demo:") and action != "run":
            raise ValueError("Individual scenarios support Run grants only.")
        if not section.startswith("demo:") and section != "demo" and action == "run":
            raise ValueError(f"{section} does not support Run grants.")
        if action == "edit" and (section, "view") not in grants:
            raise ValueError(f"{section} must include view when granting {action}.")
        if section.startswith("demo:") and ("demo", "view") not in grants:
            raise ValueError("Live scenarios must include View before granting scenario Run.")
    db.query(RolePermission).filter(RolePermission.role == role).delete()
    db.add_all(
        RolePermission(role=role, section=section, action=action)
        for section, action in sorted(grants)
    )
    db.commit()


def create_role(db: Session, name: str) -> RoleDefinition:
    role = validate_role_name(name)
    if role in LOCKED_ROLES or db.get(RoleDefinition, role) is not None:
        raise ValueError("That role name is reserved or already exists.")
    definition = RoleDefinition(name=role, built_in=False)
    db.add(definition)
    db.commit()
    return definition


def delete_role(db: Session, role: str) -> None:
    if role in LOCKED_ROLES:
        raise ValueError("Protected built-in roles cannot be deleted.")
    definition = db.get(RoleDefinition, role)
    if definition is None:
        raise LookupError("Role not found.")
    from app.models.user import User
    if db.query(User).filter(User.role == role).first():
        raise ValueError("Reassign or disable users with this role before deleting it.")
    db.query(RolePermission).filter(RolePermission.role == role).delete()
    db.delete(definition)
    db.commit()


PERMISSION_LABELS = {
    "demo": "Live scenarios",
    "demo:f1_collections": "Run F1 — Collections",
    "demo:f3_books_tally": "Run F3 — Books to Tally",
    "demo:m2_gsm_brief": "Run M2 — GSM brief",
    "demo:p1_purchase": "Run P1 — Purchase request",
    "overview": "Overview",
    "tasks": "Tasks",
    "agents": "Agents",
    "approvals": "Approvals",
    "campaigns": "Campaigns",
    "templates": "Templates",
    "optouts": "Opt-outs",
    "lanes": "Lanes",
    "tally": "Tally",
    "mail": "Mail",
    "integrations": "Integrations",
    "users": "Users",
    "activity": "Activity",
}
