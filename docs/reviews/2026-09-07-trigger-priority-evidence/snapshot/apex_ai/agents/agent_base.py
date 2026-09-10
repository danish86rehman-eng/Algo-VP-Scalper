"""
APEX AI — Agent Base Class (v9)
All multi-agents inherit from this interface.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional
from datetime import datetime, timezone


@dataclass
class AgentVote:
    agent_name: str
    vote: str           # 'BULLISH' | 'BEARISH' | 'NEUTRAL' | 'ABSTAIN'
    confidence: float   # 0.0 – 1.0
    reasoning: str = ""
    timestamp: Optional[datetime] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

    def is_directional(self) -> bool:
        return self.vote in ("BULLISH", "BEARISH")

    def __repr__(self):
        return f"{self.agent_name}: {self.vote} ({self.confidence:.2f})"


class BaseAgent(ABC):
    """Abstract base for all APEX AI agents."""

    def __init__(self, name: str):
        self.name = name
        self._last_vote: Optional[AgentVote] = None

    @abstractmethod
    def analyze(self, context: Any) -> AgentVote:
        """Analyze context and return a vote."""
        ...

    @property
    def last_vote(self) -> Optional[AgentVote]:
        return self._last_vote

    def _make_vote(self, vote: str, confidence: float, reasoning: str) -> AgentVote:
        v = AgentVote(agent_name=self.name, vote=vote,
                      confidence=round(confidence, 3), reasoning=reasoning)
        self._last_vote = v
        return v
