from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import ForeignKey, event
from sqlalchemy.orm import Mapped, Session, declared_attr, mapped_column, with_loader_criteria


DEFAULT_ORGANIZATION_ID = 1

_organization_id: ContextVar[int] = ContextVar(
    "coderai_organization_id",
    default=DEFAULT_ORGANIZATION_ID,
)
_organization_code: ContextVar[str] = ContextVar(
    "coderai_organization_code",
    default="coderai-pilot",
)
_tenant_filter_disabled: ContextVar[bool] = ContextVar(
    "coderai_tenant_filter_disabled",
    default=False,
)


def current_organization_id() -> int:
    return int(_organization_id.get())


def current_organization_code() -> str:
    return _organization_code.get()


@contextmanager
def organization_context(organization_id: int, organization_code: str) -> Iterator[None]:
    id_token = _organization_id.set(int(organization_id))
    code_token = _organization_code.set(organization_code.strip().lower())
    try:
        yield
    finally:
        _organization_code.reset(code_token)
        _organization_id.reset(id_token)


@contextmanager
def without_tenant_filter() -> Iterator[None]:
    token = _tenant_filter_disabled.set(True)
    try:
        yield
    finally:
        _tenant_filter_disabled.reset(token)


class TenantScopedMixin:
    """Adds the institution boundary shared by every business record."""

    @declared_attr
    def organization_id(cls) -> Mapped[int]:
        return mapped_column(
            ForeignKey("organizations.id"),
            nullable=False,
            index=True,
            default=current_organization_id,
        )


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(execute_state) -> None:
    if _tenant_filter_disabled.get() or not execute_state.is_orm_statement:
        return
    organization_id = current_organization_id()
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            TenantScopedMixin,
            lambda model: model.organization_id == organization_id,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _enforce_tenant_writes(session: Session, _flush_context, _instances) -> None:
    if _tenant_filter_disabled.get():
        return
    organization_id = current_organization_id()
    for instance in session.new:
        if not isinstance(instance, TenantScopedMixin):
            continue
        assigned = getattr(instance, "organization_id", None)
        if assigned is None:
            instance.organization_id = organization_id
        elif int(assigned) != organization_id:
            raise ValueError("Cross-organization inserts are forbidden.")
    for instance in session.dirty:
        if isinstance(instance, TenantScopedMixin) and int(instance.organization_id) != organization_id:
            raise ValueError("Cross-organization updates are forbidden.")
    for instance in session.deleted:
        if isinstance(instance, TenantScopedMixin) and int(instance.organization_id) != organization_id:
            raise ValueError("Cross-organization deletes are forbidden.")
