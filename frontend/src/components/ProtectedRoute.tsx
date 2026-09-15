import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../store/auth";
import { canAccessRoute } from "../lib/rbac";
import { PermissionDenied } from "./Status";

interface Props {
  children: React.ReactNode;
  requiredRoles?: string[];
  path?: string;
}

export function ProtectedRoute({ children, requiredRoles, path }: Props) {
  const session = useAuth((s) => s.session);
  const location = useLocation();

  if (!session) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  const role = session.role;
  const checkPath = path || location.pathname;

  if (!canAccessRoute(role as any, checkPath)) {
    return (
      <PermissionDenied message="You don't have permission to view this investigation. Investigator access required." />
    );
  }

  if (requiredRoles && requiredRoles.length > 0) {
    const upperRole = role?.toUpperCase();
    const allowed = requiredRoles.map((r) => r.toUpperCase()).includes(upperRole || "");
    if (!allowed) {
      return (
        <PermissionDenied message="You don't have permission to view this investigation." />
      );
    }
  }

  return <>{children}</>;
}

export default ProtectedRoute;
