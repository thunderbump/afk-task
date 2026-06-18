from __future__ import annotations

from typing import Any


WORKER_METADATA_KEYS = {
    "validation_mode",
    "validation_worker",
    "validation_worker_command",
    "worker_command",
    "validation_worker_local_command",
    "validation_worker_remote_command",
    "validation_worker_transport",
    "validation_profile",
    "validation_timeout_seconds",
    "validation_repo",
    "validation_ref",
    "validation_commit",
    "validation_evidence_dir",
}


def has_validation_metadata(metadata: dict[str, Any]) -> bool:
    if is_worker_validation_requested(metadata):
        return worker_validation_command(metadata) is not None
    return bool(metadata.get("validation_command"))


def is_worker_validation_requested(metadata: dict[str, Any]) -> bool:
    if metadata.get("validation_mode") == "worker":
        return True
    worker = metadata.get("validation_worker")
    if isinstance(worker, dict) and bool(worker):
        return True
    return any(
        metadata.get(key) not in ("", None)
        for key in ("validation_worker_command", "worker_command")
    )


def worker_validation_command(metadata: dict[str, Any]) -> str | None:
    worker = metadata.get("validation_worker")
    if isinstance(worker, dict):
        transport = normalized_transport(worker.get("transport"))
        for key in worker_command_keys(transport):
            command = normalized_command(worker.get(key))
            if command is not None:
                return command

    transport = normalized_transport(metadata.get("validation_worker_transport"))
    for key in worker_command_keys(transport, flat=True):
        command = normalized_command(metadata.get(key))
        if command is not None:
            return command
    return None


def worker_metadata_value(
    metadata: dict[str, Any],
    *,
    flat_keys: str | tuple[str, ...],
    worker_keys: str | tuple[str, ...],
    default: Any = None,
) -> Any:
    flat_key_tuple = (flat_keys,) if isinstance(flat_keys, str) else flat_keys
    worker_key_tuple = (worker_keys,) if isinstance(worker_keys, str) else worker_keys

    for key in flat_key_tuple:
        if key in metadata:
            return metadata[key]

    worker = metadata.get("validation_worker")
    if isinstance(worker, dict):
        for key in worker_key_tuple:
            if key in worker:
                return worker[key]
    return default


def worker_validation_transport(metadata: dict[str, Any]) -> str:
    worker = metadata.get("validation_worker")
    if isinstance(worker, dict):
        transport = normalized_transport(worker.get("transport"))
        if transport is not None:
            return transport
    return normalized_transport(metadata.get("validation_worker_transport")) or "local"


def worker_command_keys(
    transport: str | None,
    *,
    flat: bool = False,
) -> tuple[str, ...]:
    if transport == "remote":
        return (
            ("validation_worker_remote_command", "validation_worker_command", "worker_command")
            if flat
            else ("remote_command", "dispatch_command", "worker_command", "command")
        )
    return (
        ("validation_worker_local_command", "validation_worker_command", "worker_command")
        if flat
        else ("local_command", "worker_command", "command")
    )


def normalized_transport(value: Any) -> str | None:
    transport = normalized_command(value)
    if transport in ("remote", "ssh", "dispatch"):
        return "remote"
    if transport in ("local", None):
        return transport
    return str(transport)


def normalized_command(value: Any) -> str | None:
    if value in ("", None):
        return None
    command = str(value).strip()
    return command or None
