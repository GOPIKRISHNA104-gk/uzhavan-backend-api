"""
Voice Session Model
━━━━━━━━━━━━━━━━━━
Typed dataclass for tracking all state of a single WebSocket voice session.
Includes conversation history, emotion state, timing, and language tracking.
"""

import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional


# ─── Language Map ─────────────────────────────────────────────────────────────

LANGUAGE_TO_CODE: Dict[str, str] = {
    "tamil":    "ta-IN",
    "english":  "en-IN",
    "hindi":    "hi-IN",
    "malayalam":"ml-IN",
    "kannada":  "kn-IN",
    "telugu":   "te-IN",
}

CODE_TO_LANGUAGE: Dict[str, str] = {v: k for k, v in LANGUAGE_TO_CODE.items()}


# ─── Conversation Message ────────────────────────────────────────────────────

@dataclass
class ConversationMessage:
    role: str      # "farmer" | "ai"
    text: str
    timestamp: float = field(default_factory=time.time)
    language: str = "tamil"
    emotion: str = "neutral"


# ─── Voice Session ────────────────────────────────────────────────────────────

@dataclass
class VoiceSession:
    """
    Full state of a real-time voice conversation session.

    Lifecycle:
      created → active → [interrupted, active, ...] → ended

    5-minute inactivity timeout enforced by WebSocket handler.
    """

    # Identity
    session_id: str
    farmer_id: Optional[str] = None
    user_agent: Optional[str] = None

    # Language state
    language: str = "tamil"
    language_code: str = "ta-IN"
    language_auto_detected: bool = False
    language_confidence: float = 0.0

    # Emotion state
    emotion: str = "neutral"
    emotion_confidence: float = 0.0
    emotion_history: List[str] = field(default_factory=list)

    # Conversation
    conversation_history: List[ConversationMessage] = field(default_factory=list)
    turn_count: int = 0

    # Timing
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    total_latency_ms: int = 0
    avg_latency_ms: float = 0.0

    # Playback state
    is_bot_speaking: bool = False
    is_listening: bool = True
    interrupted_count: int = 0

    # Last processed values (for debug)
    last_transcript: str = ""
    last_intent: str = ""
    last_response_text: str = ""

    # Session flags
    is_active: bool = True
    ended_reason: Optional[str] = None

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_activity

    @property
    def session_duration_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def is_timed_out(self) -> bool:
        """Session times out after 5 minutes of inactivity."""
        return self.idle_seconds > 300

    # ── Methods ───────────────────────────────────────────────────────────────

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = time.time()

    def update_language(self, lang_name: str, confidence: float = 0.8):
        """Update session language after STT detection."""
        if lang_name in LANGUAGE_TO_CODE:
            self.language = lang_name
            self.language_code = LANGUAGE_TO_CODE[lang_name]
            self.language_auto_detected = True
            self.language_confidence = confidence

    def update_emotion(self, emotion: str, confidence: float):
        """Update emotion state and maintain history (last 5)."""
        self.emotion = emotion
        self.emotion_confidence = confidence
        self.emotion_history.append(emotion)
        if len(self.emotion_history) > 5:
            self.emotion_history = self.emotion_history[-5:]

    def add_farmer_message(self, text: str):
        """Add a farmer utterance to conversation history."""
        self.conversation_history.append(
            ConversationMessage(
                role="farmer",
                text=text,
                language=self.language,
                emotion=self.emotion,
            )
        )
        self.last_transcript = text
        self.turn_count += 1
        # Keep last 20 messages
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]

    def add_ai_message(self, text: str):
        """Add an AI response to conversation history."""
        self.conversation_history.append(
            ConversationMessage(
                role="ai",
                text=text,
                language=self.language,
                emotion=self.emotion,
            )
        )
        self.last_response_text = text

    def record_latency(self, latency_ms: int):
        """Update rolling average latency."""
        self.total_latency_ms += latency_ms
        self.avg_latency_ms = self.total_latency_ms / max(self.turn_count, 1)

    def handle_interrupt(self):
        """Record an interrupt event."""
        self.is_bot_speaking = False
        self.interrupted_count += 1
        self.touch()

    def end(self, reason: str = "normal"):
        """Mark session as ended."""
        self.is_active = False
        self.is_bot_speaking = False
        self.is_listening = False
        self.ended_reason = reason

    def to_summary(self) -> dict:
        """Return a JSON-serializable session summary."""
        return {
            "session_id": self.session_id,
            "farmer_id": self.farmer_id,
            "language": self.language,
            "language_code": self.language_code,
            "emotion": self.emotion,
            "turn_count": self.turn_count,
            "interrupted_count": self.interrupted_count,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "session_duration_s": round(self.session_duration_seconds, 1),
            "is_active": self.is_active,
            "ended_reason": self.ended_reason,
        }

    def get_history_text(self, last_n: int = 6) -> str:
        """Return recent conversation as a formatted string for prompts."""
        messages = self.conversation_history[-last_n:]
        lines = []
        for msg in messages:
            role_label = "Farmer" if msg.role == "farmer" else "AI"
            lines.append(f"{role_label}: {msg.text}")
        return "\n".join(lines) if lines else ""


# ─── Session Registry ──────────────────────────────────────────────────────────

class SessionRegistry:
    """In-memory store of all active voice sessions."""

    def __init__(self):
        self._sessions: Dict[str, VoiceSession] = {}

    def create(self, session_id: str, language: str = "tamil", farmer_id: Optional[str] = None) -> VoiceSession:
        session = VoiceSession(
            session_id=session_id,
            language=language,
            language_code=LANGUAGE_TO_CODE.get(language, "ta-IN"),
            farmer_id=farmer_id,
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[VoiceSession]:
        return self._sessions.get(session_id)

    def remove(self, session_id: str):
        self._sessions.pop(session_id, None)

    def get_all_active(self) -> List[VoiceSession]:
        return [s for s in self._sessions.values() if s.is_active]

    def purge_stale(self) -> int:
        """Remove timed-out sessions. Returns count removed."""
        stale = [sid for sid, s in self._sessions.items() if s.is_timed_out]
        for sid in stale:
            self._sessions[sid].end("timeout")
            del self._sessions[sid]
        return len(stale)

    @property
    def count(self) -> int:
        return len(self._sessions)

    def stats(self) -> dict:
        sessions = list(self._sessions.values())
        return {
            "total_sessions": len(sessions),
            "active_sessions": sum(1 for s in sessions if s.is_active),
            "languages": list({s.language for s in sessions}),
            "avg_turns": (
                sum(s.turn_count for s in sessions) / len(sessions) if sessions else 0
            ),
        }


# ─── Singleton ────────────────────────────────────────────────────────────────
session_registry = SessionRegistry()
