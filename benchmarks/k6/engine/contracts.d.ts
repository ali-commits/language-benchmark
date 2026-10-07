/** Contracts for synchronous k6 workload modules and resolved engine events. */
export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };
export interface Stage {
  id: string;
  duration: number;
  start_seconds: number;
  end_seconds: number;
  from_users: number;
  to_users: number;
  evaluate?: boolean;
}
export interface MetricFields {
  value?: number;
  unit: string;
  kind?: 'event' | 'rate' | 'gauge' | 'interval';
  scope?: 'workload' | 'backend_host' | 'container' | 'database' | 'generator';
  end_seconds?: number;
  max_gap_seconds?: number;
  normalization?: string;
  collection_interval?: number;
  formula?: string;
  numerator?: number;
  denominator?: number;
  includes_failures?: boolean;
  provider_fields?: Record<string, JsonValue>;
}
export interface WorkloadContext {
  baseUrl: string;
  parameters: Record<string, JsonValue>;
  stage: Stage;
  operation<T>(name: string, callback: () => T, success?: (result: T) => boolean): T;
  record(metric: string, fields: MetricFields): void;
}
export interface EngineConfig {
  baseUrl: string;
  stages: Stage[];
  parameters: Record<string, JsonValue>;
  gracefulRampDown?: number;
  drain?: number;
  telemetryInterval?: number;
}
