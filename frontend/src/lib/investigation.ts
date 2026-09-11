import type {
  GraphEdgeRow,
  GraphNodeRow,
  InvestigationStage,
} from "../api/client";

export function stageSummary(stage: InvestigationStage): string {
  if (stage.status === "FAILED") {
    return stage.error || "Stage failed";
  }
  if (stage.status === "PENDING") {
    return "";
  }

  const detail = (stage.detail ?? {}) as Record<string, unknown>;

  if (detail.ai_available === false) {
    return "AI unavailable";
  }

  const parts: string[] = [];

  if (typeof detail.documents_ingested === "number") {
    parts.push(`${detail.documents_ingested} documents ingested`);
  }
  if (typeof detail.documents_parsed === "number") {
    parts.push(`${detail.documents_parsed} documents parsed`);
  }
  if (typeof detail.deterministic_entities === "number") {
    parts.push(`${detail.deterministic_entities} deterministic entities`);
  }
  if (typeof detail.nlp_entities === "number") {
    parts.push(`${detail.nlp_entities} NLP entities`);
  }
  if (typeof detail.candidate_relations === "number") {
    parts.push(`${detail.candidate_relations} candidate relations`);
  }
  if (typeof detail.clusters_evaluated === "number") {
    parts.push(`${detail.clusters_evaluated} clusters evaluated`);
  }
  if (typeof detail.entities_merged === "number") {
    parts.push(`${detail.entities_merged} entities merged`);
  }
  if (typeof detail.relations_persisted === "number") {
    parts.push(`${detail.relations_persisted} relations persisted`);
  }
  if (typeof detail.nodes_persisted === "number") {
    parts.push(`${detail.nodes_persisted} nodes persisted`);
  }
  if (typeof detail.timeline_events_extracted === "number") {
    parts.push(`${detail.timeline_events_extracted} events extracted`);
  }
  if (typeof detail.temporal_anomalies === "number") {
    parts.push(`${detail.temporal_anomalies} temporal anomalies`);
  }
  if (typeof detail.patterns_detected === "number") {
    parts.push(`${detail.patterns_detected} patterns detected`);
  }
  if (typeof detail.patterns_evaluated === "number") {
    parts.push(`${detail.patterns_evaluated} patterns evaluated`);
  }

  if (parts.length > 0) {
    return parts.join(", ");
  }

  const candidates = [detail.summary, detail.description, detail.message];
  const summary = candidates.find(
    (value): value is string => typeof value === "string" && value.trim().length > 0
  );
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
  const properties = (node.properties ?? {}) as Record<string, unknown>;
  const rows: [string, string][] = [];

  const addIf = (label: string, val: unknown) => {
    const s = displayValue(val);
    if (s.length > 0) {
      rows.push([label, s]);
    }
  };

  if (node.label === "PERSON") {
    addIf("role", properties.role);
    addIf("phone", properties.phone);
    addIf("gender", properties.gender);
    addIf("dob", properties.dob);
    addIf("address", properties.address);
    addIf("occupation", properties.occupation);
  } else if (node.label === "BANK_ACCOUNT") {
    addIf("account", properties.account ?? properties.account_number);
    addIf("bank", properties.bank_code ?? properties.bank);
    addIf("ifsc", properties.ifsc);
  } else if (node.label === "PHONE") {
    addIf("number", properties.number ?? properties.phone_number);
    addIf("status", properties.status);
    addIf("first_seen", properties.first_seen);
    addIf("last_seen", properties.last_seen);
  } else if (node.label === "VEHICLE") {
    addIf("plate", properties.plate ?? properties.registration);
    addIf("make", properties.make);
    addIf("model", properties.model);
    addIf("color", properties.color);
  } else if (node.label === "LOCATION") {
    addIf("address", properties.address);
    addIf("district", properties.district);
    addIf("state_code", properties.state_code ?? properties.state);
  } else if (node.label === "ORGANIZATION") {
    addIf("name", properties.name);
    addIf("type", properties.type);
    addIf("city", properties.city);
    addIf("state", properties.state);
  } else if (node.label === "EVENT") {
    addIf("description", properties.description);
    addIf("timestamp", properties.timestamp ?? properties.ts);
    addIf("event_type", properties.event_type ?? properties.type);
  } else {
    for (const [k, v] of Object.entries(properties)) {
      addIf(k, v);
    }
  }

  return rows;
}

export function edgeSpecificRows(edge: GraphEdgeRow): [string, string][] {
  const properties = (edge.properties ?? {}) as Record<string, unknown>;
  const rows: [string, string][] = [];

  const addIf = (label: string, val: unknown) => {
    const s = displayValue(val);
    if (s.length > 0) {
      rows.push([label, s]);
    }
  };

  if (
    edge.rel_type === "CALLED" ||
    properties.call_count !== undefined ||
    properties.total_duration_seconds !== undefined
  ) {
    if (properties.call_count !== undefined) {
      addIf("calls", properties.call_count);
    }
    if (properties.total_duration_seconds !== undefined) {
      addIf("total duration (s)", properties.total_duration_seconds);
    }
  } else {
    if (properties.amount !== undefined) {
      addIf("amount (INR)", properties.amount);
    }
    if (properties.ts !== undefined || properties.timestamp !== undefined) {
      addIf("timestamp", properties.ts ?? properties.timestamp);
    }
    if (properties.channel !== undefined) {
      addIf("channel", properties.channel);
    }
    if (properties.reference !== undefined) {
      addIf("reference", properties.reference);
    }
  }

  if (rows.length === 0) {
    for (const [k, v] of Object.entries(properties)) {
      addIf(k, v);
    }
  }

  return rows;
}
