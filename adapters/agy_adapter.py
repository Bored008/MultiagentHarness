"""
Antigravity (AGY) Agent Adapter for SYNAPSE.
Subclasses BaseAgentAdapter with custom CLI entrypoint and Antigravity-specific prompt structures.
Enables Antigravity agents running across multiple laptops to discover, claim, execute, and submit
tasks within the shared SYNAPSE graph.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional
import httpx

from adapters.base import BaseAgentAdapter
from adapters.cli import build_adapter_parser, run_adapter_cli

DEFAULT_AGENT_ID = "antigravity-agent"
CLI_PROG = "python -m adapters.agy_adapter"
CLI_DESCRIPTION = "SYNAPSE Adapter for Google Antigravity (AGY)"


class AntigravityAdapter(BaseAgentAdapter):
    """
    Adapter specialized for Google Antigravity (AGY).
    Enables Antigravity agents to discover, claim, execute, and submit
    tasks within the shared SYNAPSE graph.
    """

    def __init__(
        self,
        agent_id: str = DEFAULT_AGENT_ID,
        server_url: str = "http://localhost:8000",
        ws_url: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ):
        super().__init__(
            agent_id=agent_id,
            server_url=server_url,
            ws_url=ws_url,
            client=client,
        )


def build_parser() -> argparse.ArgumentParser:
    return build_adapter_parser(
        prog=CLI_PROG,
        description=CLI_DESCRIPTION,
        default_agent_id=DEFAULT_AGENT_ID,
    )


def main(argv: Optional[List[str]] = None) -> int:
    return run_adapter_cli(
        adapter_cls=AntigravityAdapter,
        default_agent_id=DEFAULT_AGENT_ID,
        prog=CLI_PROG,
        description=CLI_DESCRIPTION,
        argv=argv,
    )


if __name__ == "__main__":
    sys.exit(main())
