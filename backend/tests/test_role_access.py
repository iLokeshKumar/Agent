import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser
from app.models.access_control import RoleDefinition, RolePermission
from app.models.user import User
from app.services.role_access import (
    create_role,
    delete_role,
    get_role_grants,
    seed_role_access,
    set_role_grants,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    RoleDefinition.__table__.create(engine)
    RolePermission.__table__.create(engine)
    User.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_default_roles_seed_once_without_overwriting_custom_grants(db):
    seed_role_access(db)
    set_role_grants(db, "finance", {("tally", "view")})

    seed_role_access(db)

    assert get_role_grants(db, "finance") == frozenset({("tally", "view")})
    assert db.get(RoleDefinition, "system").built_in is True


def test_custom_role_can_be_created_and_grants_updated(db):
    seed_role_access(db)

    role = create_role(db, "sales_manager")
    set_role_grants(
        db,
        role.name,
        {("campaigns", "view"), ("campaigns", "edit"), ("demo", "view")},
    )

    assert role.built_in is False
    assert get_role_grants(db, "sales_manager") == frozenset({
        ("campaigns", "view"),
        ("campaigns", "edit"),
        ("demo", "view"),
    })


@pytest.mark.parametrize(
    "grants",
    [
        {("campaigns", "edit")},
        {("demo:f1_collections", "run")},
        {("users", "run")},
        {("not-a-section", "view")},
    ],
)
def test_invalid_grants_are_rejected_without_replacing_existing_grants(db, grants):
    seed_role_access(db)
    create_role(db, "sales_manager")
    initial = {("campaigns", "view")}
    set_role_grants(db, "sales_manager", initial)

    with pytest.raises(ValueError):
        set_role_grants(db, "sales_manager", grants)

    assert get_role_grants(db, "sales_manager") == frozenset(initial)


def test_protected_roles_cannot_be_changed_or_deleted(db):
    seed_role_access(db)

    with pytest.raises(ValueError):
        set_role_grants(db, "admin", set())
    with pytest.raises(ValueError):
        delete_role(db, "system")


def test_unassigned_custom_role_can_be_deleted(db):
    seed_role_access(db)
    create_role(db, "sales_manager")

    delete_role(db, "sales_manager")

    assert db.get(RoleDefinition, "sales_manager") is None


def test_role_in_use_cannot_be_deleted(db):
    seed_role_access(db)
    create_role(db, "sales_manager")
    db.add(User(username="operator", role="sales_manager"))
    db.commit()

    with pytest.raises(ValueError, match="Reassign or disable users"):
        delete_role(db, "sales_manager")


def test_current_user_checks_database_grants_for_view_edit_and_scenario_run():
    user = CurrentUser(
        "id",
        "operator",
        "sales_manager",
        frozenset({
            ("campaigns", "view"),
            ("campaigns", "edit"),
            ("demo", "view"),
            ("demo:f1_collections", "run"),
        }),
    )

    assert user.can("campaigns", "view")
    assert user.can("campaigns", "edit")
    assert not user.can("campaigns", "run")
    assert user.can_run_demo("f1_collections")
    assert not user.can_run_demo("f3_books_tally")
