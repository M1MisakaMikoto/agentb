from copy import deepcopy

from fastapi import APIRouter, HTTPException

from controller.VO.result import Result
from singleton import get_settings_service


router = APIRouter(prefix="/api/settings", tags=["settings"])


def _is_sensitive(key: str, sensitive_fields: list[str]) -> bool:
    lowered = key.lower()
    return any(
        lowered == field.lower() or lowered.endswith(f"_{field.lower()}")
        for field in sensitive_fields
    )


def _redact_sensitive(value, sensitive_fields: list[str]):
    if isinstance(value, dict):
        return {
            key: "" if _is_sensitive(key, sensitive_fields) else _redact_sensitive(child, sensitive_fields)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item, sensitive_fields) for item in value]
    return value


def _preserve_sensitive(updates, current, sensitive_fields: list[str]):
    if not isinstance(updates, dict) or not isinstance(current, dict):
        return updates

    merged = deepcopy(updates)
    for key, value in list(merged.items()):
        if _is_sensitive(key, sensitive_fields) and value in (None, "", "********"):
            if key in current:
                merged[key] = current[key]
            continue
        if key in current:
            merged[key] = _preserve_sensitive(value, current[key], sensitive_fields)
    return merged


@router.get("")
def read_settings() -> Result:
    settings = get_settings_service()
    settings.reload()
    data = settings.get_all()
    sensitive_fields = list(data.get("logging", {}).get("sensitive_fields", []))
    return Result.success(_redact_sensitive(data, sensitive_fields))


@router.get("/metadata")
def read_settings_metadata() -> Result:
    return Result.success(get_settings_service().get_metadata())


@router.patch("")
def patch_settings(updates: dict) -> Result:
    settings = get_settings_service()
    settings.reload()
    current = settings.get_all()
    sensitive_fields = list(current.get("logging", {}).get("sensitive_fields", []))
    safe_updates = _preserve_sensitive(updates, current, sensitive_fields)
    try:
        settings.update_settings(safe_updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Result.success()
