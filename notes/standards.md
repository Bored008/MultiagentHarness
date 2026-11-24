# OmniHarness Engineering Standards & Invariants

## Architecture Principles
1. **Tool Agnosticism**: Neither the server nor the graph data structure assumes Claude, Codex, Cursor, or Antigravity syntax. All interactions occur via uniform MCP / REST schemas (`/task/claim`, `/task/submit`, `/requirement/update`).
2. **Blast-Radius Invalidation**: When any node `N` is mutated or invalidated (e.g. `REQ-01` requirements change), all downstream dependents `D` (`D -> N`) are flagged as `STALE_CONFLICT` with instant WebSocket notification broadcast to all agents.
3. **Idempotency & Crash Recovery**: Every task claim and submission carries a client-generated UUID `idempotency_key`. Interrupted runs or re-sent requests return the recorded result without duplicate graph mutations.
4. **Context Window Protection**: Subgraph scoping returns strictly immediate parent requirements and inputs (~1.5k tokens), eliminating full-repo context dumping.
5. **Zero AI Slop**: Strict typing (Pydantic), comprehensive test coverage (`pytest`), and clean separation of concerns between storage, graph topology, and network transport.
