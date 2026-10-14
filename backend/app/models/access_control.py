from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class RoleDefinition(Base):
    __tablename__ = "role_definitions"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    built_in: Mapped[bool] = mapped_column(nullable=False, default=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role: Mapped[str] = mapped_column(
        String(50), ForeignKey("role_definitions.name", ondelete="CASCADE"),
        primary_key=True,
    )
    section: Mapped[str] = mapped_column(String(80), primary_key=True)
    action: Mapped[str] = mapped_column(String(10), primary_key=True)
