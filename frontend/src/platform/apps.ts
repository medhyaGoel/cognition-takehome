import type { Role } from "./auth/types";

export interface NavItem {
  to: string;
  label: string;
  icon: string;
  /** Hide for users without these roles (the server still enforces them). */
  roles?: Role[];
  end?: boolean;
}

export interface AppDefinition {
  id: string;
  name: string;
  description: string;
  basePath: string;
  icon: string;
  status: "live" | "planned";
  nav: NavItem[];
}

/** Every internal tool registers here; the shell renders the switcher and sidebar from it. */
export const APPS: AppDefinition[] = [
  {
    id: "refunds",
    name: "Refunds",
    description: "Request, approve and track customer refunds",
    basePath: "/refunds",
    icon: "↺",
    status: "live",
    nav: [
      { to: "/refunds", label: "Dashboard", icon: "◧", end: true },
      { to: "/refunds/queue", label: "Queue", icon: "☰" },
      {
        to: "/refunds/new",
        label: "New refund",
        icon: "＋",
        roles: ["refund_agent", "finance_admin"],
      },
      {
        to: "/refunds/notifications",
        label: "Notifications",
        icon: "◔",
        roles: ["refund_approver", "finance_admin"],
      },
      { to: "/refunds/rules", label: "Workflow rules", icon: "⚙" },
    ],
  },
  {
    id: "feature-flags",
    name: "Feature flags",
    description: "Toggle features per business unit and environment",
    basePath: "/feature-flags",
    icon: "⚑",
    status: "live",
    nav: [{ to: "/feature-flags", label: "Flags", icon: "⚑", end: true }],
  },
  {
    id: "kyc-review",
    name: "KYC review",
    description: "Manual identity verification queue (planned)",
    basePath: "/kyc",
    icon: "◎",
    status: "planned",
    nav: [],
  },
];
