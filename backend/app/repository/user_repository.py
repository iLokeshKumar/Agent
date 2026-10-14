from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User, Permission


class UserRepository:

    def __init__(self, db: Session):
        self.db = db

    def get_by_username(
        self,
        username: str,
    ) -> User | None:
        statement = (
            select(User)
            .where(
                User.username == username,
                User.enabled.is_(True),
            )
        )

        return self.db.execute(statement).scalar_one_or_none()

    def has_permission(
        self,
        username: str,
        permission_code: str,
    ) -> bool:
        user = self.get_by_username(username)

        if user is None:
            return False

        return any(
            p.permission_code == permission_code
            for p in user.permissions
        )

    def list_users(self) -> list[User]:
        statement = (
            select(User)
            .where(User.enabled.is_(True))
            .order_by(User.username)
        )

        return list(self.db.execute(statement).scalars().all())

    def get_permission_by_code(
        self,
        permission_code: str,
    ) -> Permission | None:
        statement = (
            select(Permission)
            .where(Permission.permission_code == permission_code)
        )

        return self.db.execute(statement).scalar_one_or_none()
