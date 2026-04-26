from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi import Header, HTTPException, status

from project5_agentic_metadata_demo.app.models import Dataset


READABLE_PUBLIC_LEVELS = {"public", "internal"}
SENSITIVE_LEVELS = {"confidential", "restricted", "high"}
WRITE_ROLES = {"editor", "admin", "service"}
ADMIN_ROLES = {"admin", "service"}
VALID_ROLES = {"viewer", "editor", "admin", "service"}


@dataclass(frozen=True)
class Principal:
    user: str
    team: str
    role: str


def get_current_principal(
    x_user: str | None = Header(default=None, alias="X-User"),
    x_team: str | None = Header(default=None, alias="X-Team"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> Principal:
    if not x_user or not x_team or not x_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User, X-Team, and X-Role headers are required",
        )
    role = x_role.lower()
    if role not in VALID_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Unsupported role: {x_role}")
    return Principal(user=x_user, team=x_team.lower(), role=role)


def deny(detail: str) -> None:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def can_read_dataset(principal: Principal, dataset: Dataset) -> bool:
    if principal.role in ADMIN_ROLES:
        return True
    if dataset.owner_team == principal.team:
        return True
    return dataset.sensitivity_level in READABLE_PUBLIC_LEVELS


def filter_readable_datasets(principal: Principal, datasets: Iterable[Dataset]) -> list[Dataset]:
    return [dataset for dataset in datasets if can_read_dataset(principal, dataset)]


def require_dataset_read(principal: Principal, dataset: Dataset) -> None:
    if not can_read_dataset(principal, dataset):
        deny("Policy denied: caller is not allowed to read this dataset")


def require_dataset_create(principal: Principal, owner_team: str, sensitivity_level: str) -> None:
    if principal.role not in WRITE_ROLES:
        deny("Policy denied: write operations require editor, admin, or service role")
    if principal.role in ADMIN_ROLES:
        return
    if owner_team != principal.team:
        deny("Policy denied: editors can create metadata only for their own team")
    if sensitivity_level in SENSITIVE_LEVELS:
        deny("Policy denied: editors cannot create sensitive metadata records")


def require_dataset_update(
    principal: Principal,
    existing: Dataset,
    requested_owner_team: str | None,
    requested_sensitivity_level: str | None,
) -> None:
    if principal.role not in WRITE_ROLES:
        deny("Policy denied: write operations require editor, admin, or service role")
    if principal.role in ADMIN_ROLES:
        return
    if existing.owner_team != principal.team:
        deny("Policy denied: editors can update only their own team's metadata")
    if requested_owner_team and requested_owner_team != existing.owner_team:
        deny("Policy denied: editors cannot transfer dataset ownership")
    if existing.sensitivity_level in SENSITIVE_LEVELS or requested_sensitivity_level in SENSITIVE_LEVELS:
        deny("Policy denied: editors cannot update sensitive metadata records")


def require_dataset_delete(principal: Principal, confirmed: bool) -> None:
    if principal.role not in ADMIN_ROLES:
        deny("Policy denied: delete operations require admin or service role")
    if not confirmed:
        deny("Policy denied: delete operations require X-Confirm-Dangerous-Action: true")

