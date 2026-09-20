/**
 * Role-Based Access Control — Investigator & Viewer
 * Principle: Investigator = investigate and review, Viewer = observe and review only
 * Frontend RBAC is UX, backend RBAC is security — never trust frontend role alone
 */

export type Role = "VIEWER" | "INVESTIGATOR" | "ADMIN" | "SUPERVISOR" | "AUDITOR" | "STATION_ADMIN" | "DISTRICT_ADMIN" | "SUPER_ADMIN";

export interface PermissionMatrix {
  viewCases: boolean;
  viewPeople: boolean;
  viewRelationships: boolean;
  viewEvidence: boolean;
  viewTimeline: boolean;
  viewProvenance: boolean;
  viewInvestigatorActivity: boolean; // Viewer read-only + Investigator full
  search: boolean;
  investigateRelationship: boolean;
  createInvestigation: boolean;
  viewPatterns: boolean;
  viewAttention: boolean;
  viewAudit: boolean;
  adminAccess: boolean;
  canUpload: boolean;
  canReview: boolean;
  canExport: boolean;
  canModify: boolean;
  canDelete: boolean;
  canManageUsers: boolean;
  canManagePermissions: boolean;
  canSystemConfig: boolean;
}

const INVESTIGATOR_PERMISSIONS: PermissionMatrix = {
  viewCases: true,
  viewPeople: true,
  viewRelationships: true,
  viewEvidence: true,
  viewTimeline: true,
  viewProvenance: true,
  viewInvestigatorActivity: true, // Investigator can view own + others
  search: true,
  investigateRelationship: true,
  createInvestigation: true,
  viewPatterns: true,
  viewAttention: true,
  viewAudit: true,
  adminAccess: false,
  canUpload: true,
  canReview: true,
  canExport: true,
  canModify: true, // if authorized by backend
  canDelete: false,
  canManageUsers: false,
  canManagePermissions: false,
  canSystemConfig: false,
};

const VIEWER_PERMISSIONS: PermissionMatrix = {
  // After audit: Viewer can locate via Cases->Overview->People->Relationships->Evidence->Timeline without Search
  // Search kept Investigator-only for minimal interface
  viewCases: true,
  viewPeople: true,
  viewRelationships: true,
  viewEvidence: true,
  viewTimeline: true,
  viewProvenance: true,
  viewInvestigatorActivity: true, // Viewer read-only Investigator Activity — minimal (INV-0042, finding, evidence, strength, classification, completed)
  search: false, // Investigator-only after remove-something audit
  investigateRelationship: false,
  createInvestigation: false,
  viewPatterns: false,
  viewAttention: false,
  viewAudit: false,
  adminAccess: false,
  canUpload: false,
  canReview: false,
  canExport: false,
  canModify: false,
  canDelete: false,
  canManageUsers: false,
  canManagePermissions: false,
  canSystemConfig: false,
};

const ADMIN_PERMISSIONS: PermissionMatrix = {
  ...INVESTIGATOR_PERMISSIONS,
  viewPatterns: true,
  viewAttention: true,
  viewAudit: true,
  adminAccess: true,
  canUpload: true,
  canReview: true,
  canExport: true,
  canModify: true,
  canDelete: true,
  canManageUsers: true,
  canManagePermissions: true,
  canSystemConfig: true,
};

export function getPermissions(role: Role | null | undefined): PermissionMatrix {
  if (!role) return VIEWER_PERMISSIONS; // safest default
  const upper = role.toUpperCase() as Role;
  if (upper === "VIEWER") return VIEWER_PERMISSIONS;
  if (upper === "INVESTIGATOR") return INVESTIGATOR_PERMISSIONS;
  // All admin variants get admin permissions
  if (["ADMIN", "SUPERVISOR", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN", "AUDITOR"].includes(upper)) {
    return ADMIN_PERMISSIONS;
  }
  return VIEWER_PERMISSIONS;
}

export function isInvestigator(role: Role | null | undefined): boolean {
  if (!role) return false;
  const upper = role.toUpperCase();
  return ["INVESTIGATOR", "ADMIN", "SUPERVISOR", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"].includes(upper);
}

export function isViewer(role: Role | null | undefined): boolean {
  if (!role) return false;
  return role.toUpperCase() === "VIEWER";
}

export function isAdmin(role: Role | null | undefined): boolean {
  if (!role) return false;
  return ["ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"].includes(role.toUpperCase());
}

export function canAccessRoute(role: Role | null | undefined, path: string): boolean {
  const perms = getPermissions(role as Role);
  const lower = path.toLowerCase();

  // Public routes
  if (lower === "/login" || lower.startsWith("/login")) return true;

  // Investigator-only routes
  if (lower.startsWith("/investigate")) return perms.investigateRelationship;
  if (lower.startsWith("/patterns")) return perms.viewPatterns;
  if (lower.startsWith("/review")) return perms.canReview;
  if (lower.startsWith("/admin")) return perms.adminAccess;

  // Investigator Activity — both Viewer (read-only) and Investigator (full)
  if (lower.startsWith("/activity")) return perms.viewInvestigatorActivity;

  // Viewer + Investigator routes
  if (lower.startsWith("/cases")) return perms.viewCases;
  if (lower.startsWith("/people") || lower.startsWith("/entities")) return perms.viewPeople;
  if (lower.startsWith("/relationships")) return perms.viewRelationships;
  if (lower.startsWith("/evidence") || lower.startsWith("/documents") || lower.startsWith("/sources")) return perms.viewEvidence;
  if (lower.startsWith("/timeline")) return perms.viewTimeline;
  if (lower.startsWith("/search")) return perms.search;
  if (lower.startsWith("/graph")) return perms.viewRelationships;

  // Default deny for unknown protected routes
  return true;
}

export function getRoleLabel(role: Role | null | undefined): string {
  if (!role) return "Unknown";
  const upper = role.toUpperCase();
  if (upper === "VIEWER") return "Viewer — observe and review only";
  if (upper === "INVESTIGATOR") return "Investigator — investigate and review";
  if (upper === "ADMIN") return "Administrator";
  return upper.replace(/_/g, " ");
}

export function getRoleBadge(role: Role | null | undefined): { label: string; tone: string } {
  if (!role) return { label: "Unknown", tone: "muted" };
  const upper = role.toUpperCase();
  if (upper === "VIEWER") return { label: "VIEWER", tone: "info" };
  if (upper === "INVESTIGATOR") return { label: "INVESTIGATOR", tone: "navy" };
  if (upper === "ADMIN") return { label: "ADMIN", tone: "ok" };
  return { label: upper, tone: "muted" };
}
