from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable, Optional


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileSignature:
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class CacheEntry:
    signature: FileSignature
    text: str


class PandocConversationCache:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], CacheEntry] = {}
        self._key_locks: dict[tuple[str, str], threading.Lock] = {}
        self._conversation_tokens: dict[str, object] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _resolved_path(file_path: str) -> str:
        return os.path.normcase(os.path.realpath(os.path.abspath(file_path)))

    @staticmethod
    def _signature(file_path: str) -> FileSignature:
        stat = os.stat(file_path)
        return FileSignature(size=stat.st_size, mtime_ns=stat.st_mtime_ns)

    def get_or_load(
        self,
        conversation_id: str,
        file_path: str,
        loader: Callable[[], Optional[str]],
    ) -> Optional[str]:
        assert conversation_id, "conversation_id is required for a conversation cache"
        resolved_path = self._resolved_path(file_path)
        key = (conversation_id, resolved_path)

        with self._lock:
            conversation_token = self._conversation_tokens.setdefault(
                conversation_id,
                object(),
            )
            key_lock = self._key_locks.setdefault(key, threading.Lock())

        with key_lock:
            signature = self._signature(resolved_path)
            with self._lock:
                entry = self._entries.get(key)
                if entry and entry.signature == signature:
                    logger.info(
                        "pandoc cache hit conversation_id=%s path=%s",
                        conversation_id,
                        resolved_path,
                    )
                    return entry.text
                event = "invalidate" if entry else "miss"
                logger.info(
                    "pandoc cache %s conversation_id=%s path=%s",
                    event,
                    conversation_id,
                    resolved_path,
                )

            text = loader()
            if text is None:
                return None
            assert isinstance(text, str), "Pandoc cache loader must return str or None"

            with self._lock:
                if self._conversation_tokens.get(conversation_id) is conversation_token:
                    self._entries[key] = CacheEntry(signature=signature, text=text)
            return text

    def clear_conversation(self, conversation_id: str) -> None:
        assert conversation_id, "conversation_id is required for cache cleanup"
        with self._lock:
            self._conversation_tokens.pop(conversation_id, None)
            entry_keys = [key for key in self._entries if key[0] == conversation_id]
            lock_keys = [key for key in self._key_locks if key[0] == conversation_id]
            for key in entry_keys:
                del self._entries[key]
            for key in lock_keys:
                del self._key_locks[key]
        logger.info(
            "pandoc cache clear conversation_id=%s entries=%s",
            conversation_id,
            len(entry_keys),
        )


pandoc_conversation_cache = PandocConversationCache()


def clear_pandoc_conversation_cache(conversation_id: str) -> None:
    pandoc_conversation_cache.clear_conversation(conversation_id)
