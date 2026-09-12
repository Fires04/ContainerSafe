export interface MountInfo {
  type: string | null;
  name: string | null;
  source: string | null;
  destination: string | null;
  mode: string | null;
  rw: boolean | null;
  propagation: string | null;
}

export interface NetworkInfo {
  name: string;
  network_id: string | null;
  ip_address: string | null;
  aliases: string[] | null;
  driver?: string;
  ipam?: unknown;
  internal?: boolean;
  attachable?: boolean;
  labels?: Record<string, string>;
}

export interface Container {
  id: number;
  host_id: string;
  identity_key: string;
  docker_id: string;
  name: string;
  image: string;
  status: string;
  compose_project: string | null;
  compose_service: string | null;
  compose_working_dir: string | null;
  compose_config_files: string | null;
  labels_json: Record<string, string>;
  mounts_json: MountInfo[];
  networks_json: NetworkInfo[];
  env_json: string[];
  ports_json: Record<string, unknown>;
  restart_policy_json: Record<string, unknown>;
  command_json: string[] | null;
  entrypoint_json: string[] | null;
  first_seen_at: string;
  last_seen_at: string;
}

export interface StorageTarget {
  id: number;
  name: string;
  type: 'local' | 'rclone';
  config_json: Record<string, unknown>;
  created_at: string;
}

export interface BackupJob {
  id: number;
  host_id: string;
  identity_keys: string[];
  container_names: string[];
  display_name: string;
  schedule_cron: string;
  retention_count: number | null;
  retention_days: number | null;
  storage_target_id: number;
  storage_target_name: string | null;
  dest_subpath: string;
  include_bind_mounts: boolean;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  next_run_at: string | null;
}

export type BackupStage = 'inspecting' | 'archiving' | 'packaging' | 'uploading' | 'done';

export interface BackupRun {
  id: number;
  job_id: number;
  identity_key: string;
  batch_id: string | null;
  current_stage: BackupStage | null;
  progress_current: number | null;
  progress_total: number | null;
  started_at: string;
  finished_at: string | null;
  status: 'pending' | 'running' | 'success' | 'failed';
  archive_locator: string | null;
  size_bytes: number | null;
  log_text: string | null;
  error_message: string | null;
  job_display_name?: string | null;
}

export interface BackupBatch {
  batch_id: string;
  expected_count: number;
  runs: BackupRun[];
}

export interface CronPreview {
  valid: boolean;
  error: string | null;
  next_runs: string[];
}

export interface UploadPreview {
  identity_key: string | null;
  container_name: string | null;
  image: string | null;
  created_at: string | null;
  app_version: string | null;
  schema_version: number | null;
  compose: { project: string | null; service: string | null } | null;
  networks: { name: string; driver: string | null }[];
  volumes: { name: string; driver: string | null }[];
  mounts: { type: string; name: string | null; source: string | null; target: string; read_only: boolean }[];
  data_manifest: { kind: string; name: string; destination: string }[];
  name_conflict: boolean | null;
  name_conflict_running: boolean | null;
}

export interface UploadStageResult {
  upload_id: string;
  preview: UploadPreview;
}

export interface Agent {
  id: number;
  host_id: string;
  online: boolean;
  token_prefix: string;
  created_at: string;
  connected_at: string | null;
  last_seen_at: string | null;
  last_heartbeat_rtt_ms: number | null;
  agent_version: string | null;
}

export interface AgentEnrollResult {
  agent: Agent;
  token: string;
  env_snippet: string;
  install_url: string;
  curl_command: string;
}

export interface RestoreRun {
  id: number;
  source_archive_locator: string;
  target_identity_key: string | null;
  started_at: string;
  finished_at: string | null;
  status: 'pending' | 'running' | 'success' | 'failed';
  log_text: string | null;
  error_message: string | null;
}
