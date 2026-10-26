"""Local-only Voocks Media Studio prototype."""

from .registry import RegistryError, Role, RoleRegistry
from .store import StudioStore, StudioError
from .observer import ObserverEngine, ObserverError, ObserverFinding, ObserverRunReport
from .issues import (
    IssueCategory,
    IssueEngine,
    IssueError,
    IssueNotFound,
    IssueSeverity,
    IssueStatus,
    LocalRepairObserver,
    RepairAction,
    RepairReport,
    classify_ffmpeg_error,
)
from .pipeline import ProjectError, ProjectLinkType, ProjectNotFound, ProjectPipeline, ProjectPriority, ProjectStatus

__all__ = [
    "ObserverEngine",
    "ObserverError",
    "ObserverFinding",
    "ObserverRunReport",
    "IssueCategory",
    "IssueEngine",
    "IssueError",
    "IssueNotFound",
    "IssueSeverity",
    "IssueStatus",
    "LocalRepairObserver",
    "RepairAction",
    "RepairReport",
    "ProjectError",
    "ProjectLinkType",
    "ProjectNotFound",
    "ProjectPipeline",
    "ProjectPriority",
    "ProjectStatus",
    "RegistryError",
    "Role",
    "RoleRegistry",
    "StudioError",
    "StudioStore",
    "classify_ffmpeg_error",
]
