import type { ReactNode } from "react";
import { useAuth } from "./AuthProvider";
import type { Role } from "./types";

/**
 * Renders children only if the user holds one of `roles`.
 * UI convenience only: the server enforces every permission independently.
 */
export function Can({
  roles,
  children,
  fallback = null,
}: {
  roles: Role[];
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const { hasAnyRole } = useAuth();
  return <>{hasAnyRole(...roles) ? children : fallback}</>;
}
