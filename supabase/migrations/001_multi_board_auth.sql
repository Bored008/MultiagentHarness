-- OmniHarness / SYNAPSE Multi-Board Knowledge Graph, Friend RBAC & Agent Keys

CREATE SCHEMA IF NOT EXISTS private;
REVOKE ALL ON SCHEMA private FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS public.boards (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'building' CHECK (stage IN ('building', 'built')),
    owner_id TEXT NOT NULL,
    owner_email TEXT,
    created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    updated_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);

CREATE TABLE IF NOT EXISTS public.board_members (
    board_id TEXT NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    email TEXT,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('owner', 'editor', 'viewer')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'revoked')),
    invited_by TEXT,
    created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    updated_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    PRIMARY KEY (board_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.board_invites (
    code TEXT PRIMARY KEY,
    board_id TEXT NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('editor', 'viewer')),
    require_approval BOOLEAN NOT NULL DEFAULT TRUE,
    created_by TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    revoked_at DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS public.board_agent_keys (
    id TEXT PRIMARY KEY,
    board_id TEXT NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL,
    created_by_email TEXT,
    scopes JSONB NOT NULL DEFAULT '["tasks:read","tasks:claim","tasks:submit","nodes:create"]'::jsonb,
    revoked_at DOUBLE PRECISION,
    last_used_at DOUBLE PRECISION,
    created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);

CREATE TABLE IF NOT EXISTS public.dag_nodes (
    board_id TEXT NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('REQUIREMENT', 'DECISION', 'TASK', 'ARTIFACT')),
    status TEXT NOT NULL,
    assigned_to TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    updated_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    PRIMARY KEY (board_id, id)
);

CREATE TABLE IF NOT EXISTS public.dag_edges (
    board_id TEXT NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'relies_on',
    PRIMARY KEY (board_id, source, target)
);

CREATE TABLE IF NOT EXISTS public.dag_events (
    event_id TEXT PRIMARY KEY,
    board_id TEXT NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    idempotency_key TEXT UNIQUE,
    event_type TEXT NOT NULL,
    node_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    timestamp DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);

CREATE TABLE IF NOT EXISTS public.dag_workspace_users (
    board_id TEXT NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    tool_name TEXT NOT NULL DEFAULT 'generic_tool',
    client_host TEXT,
    status TEXT NOT NULL DEFAULT 'joined',
    last_task_id TEXT,
    joined_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    last_seen DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    PRIMARY KEY (board_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_dag_events_board_node ON public.dag_events(board_id, node_id);
CREATE INDEX IF NOT EXISTS idx_board_members_user ON public.board_members(user_id, status);
CREATE INDEX IF NOT EXISTS idx_board_agent_keys_hash ON public.board_agent_keys(key_hash);

-- Non-exposed RLS membership helpers (bound to auth.uid())
CREATE OR REPLACE FUNCTION private.has_board_access(p_board_id TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT auth.uid() IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.board_members m
        WHERE m.board_id = p_board_id
          AND m.user_id = (select auth.uid())::text
          AND m.status = 'approved'
    );
$$;

CREATE OR REPLACE FUNCTION private.can_edit_board(p_board_id TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT auth.uid() IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.board_members m
        WHERE m.board_id = p_board_id
          AND m.user_id = (select auth.uid())::text
          AND m.status = 'approved'
          AND m.role IN ('owner', 'editor')
    );
$$;

CREATE OR REPLACE FUNCTION private.is_board_owner(p_board_id TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT auth.uid() IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.boards b
        WHERE b.id = p_board_id
          AND b.owner_id = (select auth.uid())::text
    );
$$;

-- Enable RLS on all exposed tables
ALTER TABLE public.boards ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.board_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.board_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.board_agent_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dag_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dag_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dag_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dag_workspace_users ENABLE ROW LEVEL SECURITY;

-- Boards policies
CREATE POLICY boards_select ON public.boards
    FOR SELECT TO authenticated
    USING (owner_id = (select auth.uid())::text OR private.has_board_access(id));

CREATE POLICY boards_insert ON public.boards
    FOR INSERT TO authenticated
    WITH CHECK (owner_id = (select auth.uid())::text);

CREATE POLICY boards_update ON public.boards
    FOR UPDATE TO authenticated
    USING (owner_id = (select auth.uid())::text)
    WITH CHECK (owner_id = (select auth.uid())::text);

CREATE POLICY boards_delete ON public.boards
    FOR DELETE TO authenticated
    USING (owner_id = (select auth.uid())::text);

-- Board members policies
CREATE POLICY board_members_select ON public.board_members
    FOR SELECT TO authenticated
    USING (user_id = (select auth.uid())::text OR private.is_board_owner(board_id));

CREATE POLICY board_members_insert ON public.board_members
    FOR INSERT TO authenticated
    WITH CHECK (private.is_board_owner(board_id) OR user_id = (select auth.uid())::text);

CREATE POLICY board_members_update ON public.board_members
    FOR UPDATE TO authenticated
    USING (private.is_board_owner(board_id))
    WITH CHECK (private.is_board_owner(board_id));

CREATE POLICY board_members_delete ON public.board_members
    FOR DELETE TO authenticated
    USING (private.is_board_owner(board_id) OR user_id = (select auth.uid())::text);

-- Board invites policies (owner manages invites)
CREATE POLICY board_invites_select ON public.board_invites
    FOR SELECT TO authenticated
    USING (private.is_board_owner(board_id));

CREATE POLICY board_invites_write ON public.board_invites
    FOR ALL TO authenticated
    USING (private.is_board_owner(board_id))
    WITH CHECK (private.is_board_owner(board_id));

-- Board agent keys policies
CREATE POLICY board_agent_keys_select ON public.board_agent_keys
    FOR SELECT TO authenticated
    USING (private.is_board_owner(board_id) OR created_by = (select auth.uid())::text);

CREATE POLICY board_agent_keys_insert ON public.board_agent_keys
    FOR INSERT TO authenticated
    WITH CHECK (private.can_edit_board(board_id) AND created_by = (select auth.uid())::text);

CREATE POLICY board_agent_keys_update ON public.board_agent_keys
    FOR UPDATE TO authenticated
    USING (private.is_board_owner(board_id) OR created_by = (select auth.uid())::text)
    WITH CHECK (private.is_board_owner(board_id) OR created_by = (select auth.uid())::text);

-- DAG nodes, edges, events, workspace users policies
CREATE POLICY dag_nodes_select ON public.dag_nodes
    FOR SELECT TO authenticated
    USING (private.has_board_access(board_id));
CREATE POLICY dag_nodes_write ON public.dag_nodes
    FOR ALL TO authenticated
    USING (private.can_edit_board(board_id))
    WITH CHECK (private.can_edit_board(board_id));

CREATE POLICY dag_edges_select ON public.dag_edges
    FOR SELECT TO authenticated
    USING (private.has_board_access(board_id));
CREATE POLICY dag_edges_write ON public.dag_edges
    FOR ALL TO authenticated
    USING (private.can_edit_board(board_id))
    WITH CHECK (private.can_edit_board(board_id));

CREATE POLICY dag_events_select ON public.dag_events
    FOR SELECT TO authenticated
    USING (private.has_board_access(board_id));
CREATE POLICY dag_events_insert ON public.dag_events
    FOR INSERT TO authenticated
    WITH CHECK (private.can_edit_board(board_id));

CREATE POLICY dag_users_select ON public.dag_workspace_users
    FOR SELECT TO authenticated
    USING (private.has_board_access(board_id));
CREATE POLICY dag_users_write ON public.dag_workspace_users
    FOR ALL TO authenticated
    USING (private.can_edit_board(board_id))
    WITH CHECK (private.can_edit_board(board_id));
