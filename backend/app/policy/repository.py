from sqlalchemy import text
from sqlalchemy.orm import Session


class PolicyRepository:

    def __init__(self, db: Session):
        self.db = db

    def user_has_permission(
        self,
        username: str,
        permission_code: str,
    ) -> bool:
        result = self.db.execute(
            text(
                """
                SELECT 1
                FROM users u
                JOIN user_permissions up
                  ON up.user_id = u.id
                JOIN permissions p
                  ON p.id = up.permission_id
                WHERE u.username = :username
                  AND u.enabled = TRUE
                  AND p.permission_code = :permission_code
                LIMIT 1
                """
            ),
            {
                "username": username,
                "permission_code": permission_code,
            },
        )

        return result.scalar() is not None