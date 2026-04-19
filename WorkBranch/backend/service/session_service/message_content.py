import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional


TEXT_PART = "text"
IMAGE_PART = "image"
DEFAULT_IMAGE_PLACEHOLDER = "[图片]"
MAX_IMAGES_PER_MESSAGE = 5
SUPPORTED_PART_TYPES = {TEXT_PART, IMAGE_PART}


class MessageContentError(ValueError):
    pass


MessagePart = Dict[str, Any]
ChatMessage = Dict[str, Any]


def _ensure_dict(value: Any, *, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise MessageContentError(f"{field_name} 必须是对象")
    return value


def _normalize_text_part(part: Dict[str, Any]) -> MessagePart:
    text = part.get("text")
    if text is None:
        text = part.get("content")
    if text is None:
        raise MessageContentError("text part 缺少 text 字段")
    return {"type": TEXT_PART, "text": str(text)}


def _normalize_image_part(part: Dict[str, Any]) -> MessagePart:
    image_url = part.get("url") or part.get("image_url") or part.get("file_ref") or part.get("asset_id")
    if not image_url:
        raise MessageContentError("image part 缺少 url/image_url/file_ref/asset_id 字段")

    normalized = {
        "type": IMAGE_PART,
        "image_url": str(image_url),
    }

    if part.get("name"):
        normalized["name"] = str(part.get("name"))
    if part.get("mime_type"):
        normalized["mime_type"] = str(part.get("mime_type"))
    if part.get("detail"):
        normalized["detail"] = str(part.get("detail"))
    return normalized


def normalize_message_parts(value: Any) -> List[MessagePart]:
    if value is None:
        return []

    if isinstance(value, str):
        return [{"type": TEXT_PART, "text": value}]

    if isinstance(value, dict):
        if "parts" in value:
            return normalize_message_parts(value.get("parts"))
        if "content" in value and not value.get("type"):
            return normalize_message_parts(value.get("content"))
        value = [value]

    if not isinstance(value, list):
        raise MessageContentError("消息内容必须是字符串、part 对象或 part 列表")

    parts: List[MessagePart] = []
    image_count = 0
    for index, raw_part in enumerate(value):
        part = _ensure_dict(raw_part, field_name=f"part[{index}]")
        part_type = part.get("type")
        if part_type == TEXT_PART:
            normalized = _normalize_text_part(part)
        elif part_type == IMAGE_PART:
            normalized = _normalize_image_part(part)
            image_count += 1
        else:
            raise MessageContentError(f"不支持的 part 类型: {part_type}")
        parts.append(normalized)

    if image_count > MAX_IMAGES_PER_MESSAGE:
        raise MessageContentError(f"单条消息最多允许 {MAX_IMAGES_PER_MESSAGE} 张图片")

    return parts


def has_image_parts(parts: Any) -> bool:
    for part in normalize_message_parts(parts):
        if part.get("type") == IMAGE_PART:
            return True
    return False


def image_part_placeholder(part: Dict[str, Any]) -> str:
    name = part.get("name")
    if name:
        return f"[图片: {name}]"
    return DEFAULT_IMAGE_PLACEHOLDER


def parts_to_plain_text(parts: Any) -> str:
    plain_parts: List[str] = []
    for part in normalize_message_parts(parts):
        if part.get("type") == TEXT_PART:
            plain_parts.append(str(part.get("text", "")))
        elif part.get("type") == IMAGE_PART:
            plain_parts.append(image_part_placeholder(part))
    return " ".join(item for item in plain_parts if item).strip()


def serialize_parts(parts: Any) -> str:
    normalized = normalize_message_parts(parts)
    return json.dumps(normalized, ensure_ascii=False)


def try_deserialize_parts(value: Any) -> Optional[List[MessagePart]]:
    if value is None:
        return []
    if isinstance(value, list):
        return normalize_message_parts(value)
    if isinstance(value, dict):
        return normalize_message_parts(value)
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return []
    if not (stripped.startswith("[") or stripped.startswith("{")):
        return None

    try:
        parsed = json.loads(stripped)
    except Exception:
        return None
    return normalize_message_parts(parsed)


def deserialize_parts(value: Any) -> List[MessagePart]:
    parsed = try_deserialize_parts(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str):
        return normalize_message_parts(value)
    raise MessageContentError("无法解析消息内容")


def normalize_user_content(value: Any) -> List[MessagePart]:
    return deserialize_parts(value)


def normalize_chat_message(role: str, value: Any) -> ChatMessage:
    parts = normalize_message_parts(value)
    return {
        "role": role,
        "parts": parts,
        "content": parts_to_plain_text(parts),
    }


def normalize_chat_messages(messages: List[Dict[str, Any]]) -> List[ChatMessage]:
    normalized: List[ChatMessage] = []
    for message in messages or []:
        if not isinstance(message, dict):
            raise MessageContentError("消息必须是对象")
        role = str(message.get("role", "user"))
        if "parts" in message:
            normalized.append(normalize_chat_message(role, message.get("parts")))
            continue
        normalized.append(normalize_chat_message(role, message.get("content", "")))
    return normalized


def get_message_text(message: Dict[str, Any]) -> str:
    if not isinstance(message, dict):
        return parts_to_plain_text(message)
    if "parts" in message:
        return parts_to_plain_text(message.get("parts"))
    return parts_to_plain_text(message.get("content", ""))


def get_message_parts(message: Dict[str, Any]) -> List[MessagePart]:
    if not isinstance(message, dict):
        return normalize_message_parts(message)
    if "parts" in message:
        return normalize_message_parts(message.get("parts"))
    return normalize_message_parts(message.get("content", ""))


def build_prompt_safe_text(value: Any) -> str:
    if isinstance(value, list):
        try:
            return parts_to_plain_text(value)
        except MessageContentError:
            text_parts: List[str] = []
            for item in value:
                if isinstance(item, dict) and "role" in item:
                    role = item.get("role", "user")
                    text_parts.append(f"{role}: {get_message_text(item)}")
                else:
                    text_parts.append(str(item))
            return "\n".join(text_parts).strip()
    if isinstance(value, dict):
        if "role" in value:
            return get_message_text(value)
        if "parts" in value or "content" in value:
            return get_message_text(value)
    if value is None:
        return ""
    if isinstance(value, str):
        return parts_to_plain_text(value)
    return str(value)


def build_user_message(role: str, value: Any) -> Dict[str, Any]:
    return normalize_chat_message(role, value)


def _is_external_image_ref(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("data:")


def _file_to_data_url(file_path: Path) -> str:
    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(file_path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def resolve_runtime_parts(parts: Any, workspace_dir: str | None = None) -> List[MessagePart]:
    normalized = normalize_message_parts(parts)
    if not workspace_dir:
        return normalized

    workspace_root = Path(workspace_dir).resolve()
    resolved_parts: List[MessagePart] = []
    for part in normalized:
        if part.get("type") != IMAGE_PART:
            resolved_parts.append(part)
            continue

        image_ref = str(part.get("image_url", ""))
        if not image_ref or _is_external_image_ref(image_ref):
            resolved_parts.append(part)
            continue

        candidate = (workspace_root / image_ref).resolve()
        if workspace_root != candidate and workspace_root not in candidate.parents:
            raise MessageContentError(f"图片引用越界: {image_ref}")
        if not candidate.exists() or not candidate.is_file():
            raise MessageContentError(f"图片引用不存在: {image_ref}")

        next_part = dict(part)
        next_part["image_url"] = _file_to_data_url(candidate)
        if not next_part.get("name"):
            next_part["name"] = candidate.name
        resolved_parts.append(next_part)

    return resolved_parts
