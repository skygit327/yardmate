export interface User {
  id: string;
  username: string;
  display_name: string;
}

export interface Session {
  user: User;
  csrf_token: string;
  expires_at: number;
}

export interface Condition {
  field: string;
  label: string;
  operator: 'lt' | 'lte' | 'gt' | 'gte';
  limit: string;
  unit: 'mm' | 'µm';
  warning_margin: string | null;
}

export interface Rule {
  id: string;
  area: string;
  item: string;
  kind: 'NUMERIC' | 'PROHIBITED' | 'NOT_APPLICABLE' | 'UNRESOLVED';
  conditions: Condition[];
  source: string;
}

export interface ChangeEntry {
  at: string;
  actor: string;
  actor_user_id: string | null;
  kind: 'CREATED' | 'REVISED' | 'ACTIVATED' | 'SUPERSEDED';
  summary: string;
  instruction: string;
  request_file: string | null;
  request_filename: string | null;
  operations: Operation[];
  interpreter: string | null;
}

export interface Standard {
  id: string;
  customer: string;
  part_number: string;
  product_name: string;
  document_number: string;
  revision: string;
  provenance: 'SYNTHETIC' | 'INTERNAL';
  status: 'DRAFT' | 'ACTIVE' | 'SUPERSEDED';
  source_file: string | null;
  rules: Rule[];
  activated_by?: string | null;
  family_id?: string | null;
  version?: number;
  previous_id?: string | null;
  created_at?: string | null;
  activated_at?: string | null;
  superseded_by?: string | null;
  history?: ChangeEntry[];
}

export interface StandardVersion extends Standard {
  inspection_count: number;
}

export interface Operation {
  op: 'update_condition' | 'add_condition' | 'remove_condition' | 'update_rule' | 'add_rule' | 'remove_rule' | 'set_header';
  rule_id: string | null;
  field: string | null;
  values: Record<string, string | null>;
  condition: Condition | null;
  rule: Rule | null;
  reason: string;
}

export interface RuleDiff {
  rule_id: string;
  area: string;
  item: string;
  change: 'added' | 'removed' | 'changed';
  before: string;
  after: string;
  fields: { field: string; before: string; after: string }[];
  conditions: { field: string; change: string; before: string; after: string }[];
}

export interface StandardDiff {
  before_id: string | null;
  after_id: string;
  before_revision: string | null;
  after_revision: string;
  header: { field: string; before: string; after: string }[];
  rules: RuleDiff[];
  changed: boolean;
}

export interface RevisionProposal {
  base_id: string;
  base_revision: string;
  base_status: Standard['status'];
  suggested_revision: string;
  summary: string;
  operations: Operation[];
  notes: string[];
  interpreter: string;
  diff: StandardDiff | null;
  instruction: string;
  attachment: { stored_name: string; filename: string; sha256: string } | null;
  extracted_text: string | null;
  llm_external: boolean;
}

export interface Observation {
  measurements?: Record<string, string | null>;
  units?: Record<string, string>;
  defect_present?: boolean | null;
}

export interface ItemResult {
  rule_id: string;
  area: string;
  item: string;
  auto_result: string;
  final_result: string | null;
  warning: boolean;
  criteria: string;
  measured: string;
  explanation: string;
  source: string;
  review_reason: string;
}

export interface Inspection {
  inspection_id: string;
  created_at: string;
  standard: Standard;
  inputs: {
    standard_id: string;
    lot: string;
    inspector: string;
    inspection_date: string;
    observations: Record<string, Observation>;
  };
  input_revision: number;
  revision: number;
  status: string;
  overall_result: string;
  item_results: ItemResult[];
  pending: { id: string; kind: string; message: string } | null;
  error: string | null;
  approver: string;
  report: { revision: string; approved: boolean; files?: Record<string, { path: string; sha256: string }>; approval?: { user_id: string | null; name: string; at: string } | null } | null;
  events: { action: string; actor: string; actor_user_id?: string | null; reason: string; at: string; input_revision: number }[];
}

export type InspectionSummary = Pick<Inspection, 'inspection_id' | 'created_at' | 'standard' | 'inputs' | 'revision' | 'status' | 'overall_result'>;

export type ReplyLanguage = 'en' | 'ko' | 'zh';

export interface Contact { name: string; role: string; email: string; phone: string }

export interface CustomerRecord {
  id: string;
  name: string;
  code: string;
  country: string;
  reply_language: ReplyLanguage;
  contacts: Contact[];
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface CustomerOverview {
  name: string;
  registered: boolean;
  record: CustomerRecord | null;
  parts: { part_number: string; product_name: string }[];
  active_standards: number;
  requests: number;
  open_requests: number;
  inspections: number;
  failed_inspections: number;
  last_activity: string | null;
}

export interface RequestSummary {
  id: string;
  subject: string;
  customer: string;
  part_numbers: string[];
  channel: string;
  status: 'OPEN' | 'IN_PROGRESS' | 'RESPONDED' | 'CLOSED';
  category: string;
  priority: 'LOW' | 'NORMAL' | 'HIGH';
  received_at: string;
  updated_at: string;
  due_date: string | null;
  attachments: number;
}

export interface TimelineEntry {
  at: string;
  kind: string;
  title: string;
  detail: string;
  actor: string;
  part_numbers: string[];
  ref: { type: 'request' | 'standard' | 'inspection'; id: string };
}

export interface CustomerDetail extends CustomerOverview {
  part_filter: string | null;
  parts: (CustomerOverview['parts'][number] & {
    standard: { id: string; lineage: string; document_number: string; revision: string } | null;
    draft_revision: string | null;
    versions: number;
    inspections: number;
    last_result: string | null;
    open_requests: number;
  })[];
  request_list: RequestSummary[];
  inspection_list: { id: string; lot: string; part_number: string; revision: string; overall_result: string; status: string; created_at: string }[];
  timeline: TimelineEntry[];
}

export interface RequestAnalysis {
  summary: string;
  key_points: string[];
  category: string;
  priority: string;
  mentions: { customers: string[]; parts: string[]; lots: string[]; items: { standard_id: string; rule_id: string; area: string; item: string; criteria: string }[] };
  related_standards: { id: string; lineage: string; customer: string; part_number: string; product_name: string; document_number: string; revision: string; status: string }[];
  related_inspections: { id: string; lot: string; date: string; overall_result: string; status: string; part_number: string; revision: string; matched: boolean }[];
  findings: { code: string; template: string; params: Record<string, string>; text: string }[];
  missing: string[];
  proposed_actions: { kind: string; label: string; template: string; params: Record<string, string | string[]>; target_id?: string | null; instruction?: string | null }[];
  interpreter: string;
  notes: string[];
  language: ReplyLanguage;
  generated_at: string;
}

export interface CustomerRequest extends Omit<RequestSummary, 'attachments'> {
  sender: string;
  body: string;
  attachments: { stored_name: string; filename: string; sha256: string; size: number }[];
  analysis: RequestAnalysis | null;
  response_language: ReplyLanguage;
  response_draft: string;
  final_response: string;
  events: { at: string; actor: string; kind: string; note: string; changes: Record<string, { before: unknown; after: unknown }> }[];
}
