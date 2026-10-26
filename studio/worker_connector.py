"""Provider-independent, offline contracts for future worker adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from .worker_contracts import (
    Accelerator,
    DirectorAuthorization,
    JobLease,
    JobManifest,
    JobResult,
    WorkerRegistration,
    WorkerState,
    WorkloadKind,
)


WORKER_ADAPTER_CONTRACT_VERSION = 1


@dataclass(frozen=True)
class WorkerCapabilityProfile:
    profile_version: int
    worker_id: str
    availability: str
    workloads: tuple[WorkloadKind, ...]
    cpu_cores: int
    memory_mb: int
    accelerators: tuple[Accelerator, ...]
    vram_mb: int
    processing_types: tuple[str, ...]
    operations: tuple[str, ...]
    language_tags: tuple[str, ...]
    model_ids: tuple[str, ...]
    expires_at: datetime
    is_synthetic: bool = True


@dataclass(frozen=True)
class CapabilityMatch:
    worker_id: str
    hardware_compatible: bool
    available_for_lease: bool
    reasons: tuple[str, ...]


@runtime_checkable
class WorkerAdapter(Protocol):
    """Versioned transport-neutral contract; implementations are future work."""

    adapter_contract_version: int

    def capability_profile(self) -> WorkerCapabilityProfile:
        """Return a bounded, declarative capability snapshot."""
        ...

    def set_online(self, online: bool, *, changed_at: datetime) -> None:
        """Change manual availability; adapters must not self-authorize jobs."""
        ...

    def claim(
        self,
        manifest: JobManifest,
        authorization: DirectorAuthorization,
        *,
        lease: JobLease,
        now: datetime,
    ) -> None:
        """Accept only a queue-issued lease for a Director-authorized manifest."""
        ...

    def heartbeat(
        self, job_id: str, lease_id: str, *, worker_id: str, now: datetime
    ) -> None:
        """Renew only the current queue-owned lease."""
        ...

    def submit_result(self, result: JobResult, *, now: datetime) -> None:
        """Submit metadata to the control plane for byte-level verification."""
        ...


def profile_from_registration(
    registration: WorkerRegistration,
    *,
    cpu_cores: int,
    memory_mb: int,
    processing_types: tuple[str, ...],
) -> WorkerCapabilityProfile:
    return WorkerCapabilityProfile(
        profile_version=1,
        worker_id=registration.worker_id,
        availability=registration.state.value,
        workloads=registration.workloads,
        cpu_cores=cpu_cores,
        memory_mb=memory_mb,
        accelerators=(
            (Accelerator.NVIDIA_T4,)
            if registration.vram_mb > 0
            else ()
        ),
        vram_mb=registration.vram_mb,
        processing_types=processing_types,
        operations=registration.operations,
        language_tags=registration.language_tags,
        model_ids=registration.model_ids,
        expires_at=registration.expires_at,
    )


def offline_colab_t4_profile(*, expires_at: datetime) -> WorkerCapabilityProfile:
    """Metadata-only Colab compatibility fixture; never a live registration."""
    return WorkerCapabilityProfile(
        profile_version=1,
        worker_id="offline-mock-colab-t4",
        availability="offline_mock",
        workloads=(WorkloadKind.GPU_INFERENCE,),
        cpu_cores=2,
        memory_mb=12 * 1024,
        accelerators=(Accelerator.NVIDIA_T4,),
        vram_mb=16 * 1024,
        processing_types=("transcribe", "translate", "analyze_media"),
        operations=("transcribe", "translate", "analyze_media"),
        language_tags=("hi-IN", "ta-IN", "bn-IN", "mr-IN"),
        model_ids=("offline-mock-model-v1",),
        expires_at=expires_at,
        is_synthetic=True,
    )


def match_job_to_worker(
    manifest: JobManifest,
    profile: WorkerCapabilityProfile,
    *,
    now: datetime,
) -> CapabilityMatch:
    reasons: list[str] = []
    if profile.profile_version != 1:
        reasons.append("unsupported-profile-version")
    if manifest.workload not in profile.workloads:
        reasons.append("workload-unsupported")
    if profile.cpu_cores < manifest.resources.cpu_cores:
        reasons.append("insufficient-cpu-cores")
    if profile.memory_mb < manifest.resources.memory_mb:
        reasons.append("insufficient-memory")
    if manifest.resources.accelerator is not Accelerator.NONE:
        if manifest.resources.accelerator not in profile.accelerators:
            reasons.append("required-accelerator-unavailable")
        if profile.vram_mb < manifest.resources.min_vram_mb:
            reasons.append("insufficient-vram")
    required_operations = set(manifest.capabilities.operations)
    if not required_operations <= set(profile.operations):
        reasons.append("processing-operation-unsupported")
    if not set(manifest.capabilities.language_tags) <= set(profile.language_tags):
        reasons.append("language-capability-unsupported")
    if not set(manifest.capabilities.model_ids) <= set(profile.model_ids):
        reasons.append("model-capability-unsupported")
    if profile.processing_types and not required_operations <= set(profile.processing_types):
        reasons.append("processing-type-unsupported")

    hardware_compatible = not reasons
    available_for_lease = (
        hardware_compatible
        and profile.availability == WorkerState.AVAILABLE.value
        and now < profile.expires_at
    )
    if profile.availability != WorkerState.AVAILABLE.value:
        reasons.append("worker-unavailable")
    if now >= profile.expires_at:
        reasons.append("worker-registration-expired")
    return CapabilityMatch(
        worker_id=profile.worker_id,
        hardware_compatible=hardware_compatible,
        available_for_lease=available_for_lease,
        reasons=tuple(reasons),
    )
