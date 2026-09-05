import type {
  GraphEdgeRow,
  GraphNodeRow,
  InvestigationStage,
} from "../api/client";

export function stageSummary(stage: InvestigationStage): string {
  const detail = stage.detail;
  const candidates = [detail.summary, detail.description, detail.message];
  const summary = candidates.find((value): value is string => typeof value === "string" && value.trim().length > 0);
  return summary ?? "";
}

export function relLabel(relType: string): string {
  return relType.replaceAll("_", " ").toLowerCase();
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function typeSpecificRows(node: GraphNodeRow): [string, string][] {
  const properties = node.properties ?? {};
  const keysByType: Record<string, string[]> = {
    PERSON: ["gender", "dob", "address", "occupation"],
    PHONE: ["number", "status", "first_seen", "last_seen"],
    BANK_ACCOUNT: ["number", "ifsc", "bank"],
    VEHICLE: ["plate", "make", "model", "color"],
    LOCATION: ["address", "district", "state_code"],
    ORGANIZATION: ["name", "type", "city", "state"],
    EVENT: ["description", "timestamp", "event_type"],
  };
  const keys = keysByType[node.label] ?? [];
  return keys
    .map((key) => [key, displayValue(properties[key])] as [string, string])
    .filter(([, value]) => value.length > 0);
}

export function edgeSpecificRows(edge: GraphEdgeRow): [string, string][] {
  const properties = edge.properties ?? {};
  const keys = [
    "call_count",
    "first_ts",
    "last_ts",
    "amount",
    "transfer_count",
    "total_amount",
    "channel",
    "direction",
    "distance_km",
  ];
  return keys
    .map((key) => [key, displayValue(properties[key])] as [string, string])
    .filter(([, value]) => value.length > 0);
}
