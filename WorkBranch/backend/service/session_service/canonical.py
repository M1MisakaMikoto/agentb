from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class SegmentType(Enum):
    THINKING_START = "thinking_start"
    THINKING_DELTA = "thinking_delta"
    THINKING_END = "thinking_end"
    THINKING = "thinking"
    
    CHAT_START = "chat_start"
    CHAT_DELTA = "chat_delta"
    CHAT_END = "chat_end"
    
    TEXT_START = "text_start"
    TEXT_DELTA = "text_delta"
    TEXT_END = "text_end"
    
    PLAN_START = "plan_start"
    PLAN_DELTA = "plan_delta"
    PLAN_END = "plan_end"
    
    STATE_CHANGE = "state_change"
    TOOL_CALL = "tool_call"
    TOOL_RES = "tool_res"
    ERROR = "error"
    DONE = "done"
    CONVERSATION_HANDOFF = "conversation_handoff"
    COMPRESSION_START = "compression_start"
    COMPRESSION_END = "compression_end"


@dataclass
class Message:
    role: str
    message_id: str
    conversation_id: str
    session_id: str
    workspace_id: str
    type: SegmentType
    content: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "type": self.type.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except ValueError:
                timestamp = datetime.now()
        else:
            timestamp = datetime.now()

        return cls(
            role=data["role"],
            message_id=data["message_id"],
            conversation_id=data["conversation_id"],
            session_id=data["session_id"],
            workspace_id=data["workspace_id"],
            type=SegmentType(data["type"]),
            content=data.get("content", ""),
            timestamp=timestamp,
            metadata=data.get("metadata", {}),
        )


class MessageBuilder:
    @staticmethod
    def build(
        role: str,
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        msg_type: SegmentType,
        content: str = "",
        metadata: Dict[str, Any] = None
    ) -> Message:
        return Message(
            role=role,
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            type=msg_type,
            content=content,
            metadata=metadata or {},
        )
    
    @staticmethod
    def thinking_start(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.THINKING_START,
            content="",
            metadata=metadata,
        )
    
    @staticmethod
    def thinking_delta(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        content: str
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.THINKING_DELTA,
            content=content,
        )
    
    @staticmethod
    def thinking_end(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.THINKING_END,
            content="",
            metadata=metadata,
        )
    
    @staticmethod
    def chat_start(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.CHAT_START,
            content="",
            metadata=metadata,
        )
    
    @staticmethod
    def chat_delta(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        content: str
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.CHAT_DELTA,
            content=content,
        )
    
    @staticmethod
    def chat_end(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.CHAT_END,
            content="",
            metadata=metadata,
        )
    
    @staticmethod
    def text_start(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.TEXT_START,
            content="",
            metadata=metadata,
        )
    
    @staticmethod
    def text_delta(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        content: str
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.TEXT_DELTA,
            content=content,
        )
    
    @staticmethod
    def text_end(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.TEXT_END,
            content="",
            metadata=metadata,
        )
    
    @staticmethod
    def plan_start(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.PLAN_START,
            content="",
            metadata=metadata,
        )
    
    @staticmethod
    def plan_delta(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        content: str
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.PLAN_DELTA,
            content=content,
        )
    
    @staticmethod
    def plan_end(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.PLAN_END,
            content="",
            metadata=metadata,
        )
    
    @staticmethod
    def state_change(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.STATE_CHANGE,
            content="",
            metadata=metadata,
        )
    
    @staticmethod
    def tool_call(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.TOOL_CALL,
            content="",
            metadata=metadata,
        )
    
    @staticmethod
    def tool_res(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        content: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.TOOL_RES,
            content=content,
            metadata=metadata,
        )
    
    @staticmethod
    def error(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        content: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.ERROR,
            content=content,
            metadata=metadata,
        )
    
    @staticmethod
    def done(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.DONE,
            content="",
            metadata=metadata,
        )

    @staticmethod
    def conversation_handoff(
        message_id: str,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        metadata: dict = None
    ) -> Message:
        return MessageBuilder.build(
            role="assistant",
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            msg_type=SegmentType.CONVERSATION_HANDOFF,
            content="",
            metadata=metadata,
        )


ContentBlockType = SegmentType
CanonicalMessage = Message
CanonicalBuilder = MessageBuilder
