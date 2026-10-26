"""Validation and loading for the documented functional role registry."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


ROLE_ID_PATTERN = re.compile(r"^D\d{2}-R\d{2}$")
ROLE_ROW_PATTERN = re.compile(r"^\| (D\d{2}-R\d{2}) \| (.+?) \| (.*?) \| (.+?) \|$")
DEPARTMENT_PATTERN = re.compile(r"^## (D\d{2}) — (.+?) \(\d+\)$")


class RegistryError(ValueError):
    """Raised when a registry is malformed or references unknown roles."""


@dataclass(frozen=True)
class Role:
    role_id: str
    department_id: str
    title: str
    responsibility: str
    dependencies: tuple[str, ...]
    verification: str


class RoleRegistry:
    def __init__(self, roles: dict[str, Role]) -> None:
        self._roles = dict(roles)
        self._validate()

    @classmethod
    def from_markdown(cls, path: str | Path, expected_count: int = 220) -> "RoleRegistry":
        roles: dict[str, Role] = {}
        current_department: str | None = None

        for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            department_match = DEPARTMENT_PATTERN.match(line)
            if department_match:
                current_department = department_match.group(1)
                continue

            row_match = ROLE_ROW_PATTERN.match(line)
            if not row_match:
                continue
            role_id, role_text, dependency_text, verification = row_match.groups()
            if current_department is None:
                raise RegistryError(f"Role {role_id} appears before a department heading at line {line_number}")
            if role_id in roles:
                raise RegistryError(f"Duplicate role ID: {role_id}")
            if role_id[:3] != current_department:
                raise RegistryError(f"Role {role_id} is under the wrong department at line {line_number}")
            title, separator, responsibility = role_text.partition(" — ")
            if not separator or not title or not responsibility:
                raise RegistryError(f"Role {role_id} has an invalid title/responsibility at line {line_number}")
            if dependency_text.strip() == "—":
                dependencies = ()
            else:
                dependency_parts = [part.strip() for part in dependency_text.split(",")]
                if any(not ROLE_ID_PATTERN.fullmatch(part) for part in dependency_parts):
                    raise RegistryError(f"Role {role_id} has malformed dependencies at line {line_number}")
                dependencies = tuple(dependency_parts)
            roles[role_id] = Role(
                role_id=role_id,
                department_id=current_department,
                title=title,
                responsibility=responsibility,
                dependencies=dependencies,
                verification=verification,
            )

        if len(roles) != expected_count:
            raise RegistryError(f"Expected {expected_count} role rows, found {len(roles)}")
        return cls(roles)

    def _validate(self) -> None:
        for role_id, role in self._roles.items():
            if not ROLE_ID_PATTERN.fullmatch(role_id) or role.role_id != role_id:
                raise RegistryError(f"Invalid role ID: {role_id}")
            if role.department_id != role_id[:3]:
                raise RegistryError(f"{role_id} is assigned to the wrong department")
            missing = set(role.dependencies) - self._roles.keys()
            if missing:
                raise RegistryError(f"{role_id} references unknown roles: {', '.join(sorted(missing))}")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(role_id: str) -> None:
            if role_id in visiting:
                raise RegistryError(f"Dependency cycle includes {role_id}")
            if role_id in visited:
                return
            visiting.add(role_id)
            for dependency in self._roles[role_id].dependencies:
                visit(dependency)
            visiting.remove(role_id)
            visited.add(role_id)

        for role_id in self._roles:
            visit(role_id)

    def get(self, role_id: str) -> Role:
        try:
            return self._roles[role_id]
        except KeyError as exc:
            raise RegistryError(f"Unknown role ID: {role_id}") from exc

    def roles_for_department(self, department_id: str) -> tuple[Role, ...]:
        return tuple(
            role for role in self._roles.values() if role.department_id == department_id
        )

    def all_roles(self) -> tuple[Role, ...]:
        return tuple(self._roles.values())

    def __len__(self) -> int:
        return len(self._roles)

    def __contains__(self, role_id: str) -> bool:
        return role_id in self._roles
