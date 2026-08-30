export type Conversation = {
  id: string;
  title: string;
  status: "active" | "archived";
  workspace: string;
  profile_name: string | null;
  profile_revision: number | null;
  profile_spec_digest: string | null;
  updated_at: string;
  participants?: string[];
  group_mode?: "sequential" | "parallel" | "coordinator";
  coordinator_profile?: string | null;
};

export type Message = {
  id: string;
  role: "user" | "assistant" | "tool" | "system-event";
  content: string;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type Profile = {
  name: string;
  revision: number;
  description: string;
  trust: string;
  source: string;
  source_scope?: "managed" | "project" | "portable" | "universal" | "user";
  editable: boolean;
  default: boolean;
  provider: string;
  model: string;
  tool_count: number;
  skill_count: number;
  adjustment_count: number;
  spec_digest: string;
};

export type Settings = {
  model: {
    provider: string;
    model: string;
    small_model: string;
    credential_configured: boolean;
  };
  runtime: Record<string, unknown>;
  agent_profiles: {
    enabled: boolean;
    default_profile: string | null;
    writeback: string;
  };
  memory: { local_enabled: boolean; shared_enabled: boolean };
  managed_overlay_active: boolean;
  write_target: string;
};
