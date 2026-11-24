"""
OmniHarness Adapters Module.
"""
from adapters.base import BaseAgentAdapter, ConflictListener

__all__ = [
    "BaseAgentAdapter",
    "ConflictListener",
    "ClaudeCodeAdapter",
    "CodexAdapter",
]


def __getattr__(name: str):
    if name == "ClaudeCodeAdapter":
        from adapters.claude_adapter import ClaudeCodeAdapter
        return ClaudeCodeAdapter
    if name == "CodexAdapter":
        from adapters.codex_adapter import CodexAdapter
        return CodexAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
