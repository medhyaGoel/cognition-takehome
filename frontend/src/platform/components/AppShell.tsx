import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router";
import { APPS, type AppDefinition } from "../apps";
import { useAuth } from "../auth/AuthProvider";
import { DEMO_USERS } from "../auth/types";
import { Chip, humanize } from "./StatusBadge";

function currentApp(pathname: string): AppDefinition {
  return APPS.find((a) => a.status === "live" && pathname.startsWith(a.basePath)) ?? (APPS[0] as AppDefinition);
}

function initials(name: string): string {
  const parts = name.split(/[\s@.]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

function AppSwitcher({ active }: { active: AppDefinition }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div className="app-switcher" ref={ref}>
      <button
        type="button"
        className="app-switcher-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="app-icon">{active.icon}</span>
        <span className="app-switcher-name">{active.name}</span>
        <span className="muted" aria-hidden>
          ⌄
        </span>
      </button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-label">Apps</div>
          {APPS.map((a) =>
            a.status === "live" ? (
              <NavLink
                key={a.id}
                to={a.basePath}
                role="menuitem"
                className="menu-item"
                onClick={() => setOpen(false)}
              >
                <span className="app-icon">{a.icon}</span>
                <span className="menu-item-text">
                  <span>{a.name}</span>
                  <span className="muted small">{a.description}</span>
                </span>
              </NavLink>
            ) : (
              <div key={a.id} role="menuitem" aria-disabled="true" className="menu-item disabled">
                <span className="app-icon">{a.icon}</span>
                <span className="menu-item-text">
                  <span>
                    {a.name} <Chip>Soon</Chip>
                  </span>
                  <span className="muted small">{a.description}</span>
                </span>
              </div>
            ),
          )}
        </div>
      )}
    </div>
  );
}

function UserMenu() {
  const { me, switchUser } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div className="user-menu" ref={ref}>
      <div className="user-meta">
        <div className="user-bus" aria-label="Business units">
          {me.business_units.length === 0 ? (
            <Chip tone="danger">No business unit</Chip>
          ) : (
            me.business_units.map((bu) => (
              <Chip key={bu} tone="accent">
                {bu}
              </Chip>
            ))
          )}
        </div>
        <div className="user-roles" aria-label="Roles">
          {me.roles.map((r) => (
            <Chip key={r}>{humanize(r)}</Chip>
          ))}
        </div>
      </div>
      <button
        type="button"
        className="user-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="avatar" aria-hidden>
          {initials(me.display_name)}
        </span>
        <span className="user-name">
          <span>{me.display_name}</span>
          <span className="muted small">{me.subject}</span>
        </span>
      </button>
      {open && (
        <div className="menu menu-right" role="menu">
          <div className="menu-label">Signed in as {me.subject}</div>
          {
            <>
              <button
                type="button"
                role="menuitem"
                className="menu-item"
                onClick={() => void switchUser()}
              >
                Switch user (sign out at IdP)
              </button>
              {import.meta.env.DEV && (
                <div className="dev-hint">
                  <strong>Local demo users</strong>
                  <ul>
                    {DEMO_USERS.map((u) => (
                      <li key={u.username}>
                        <code>{u.username}</code> <span className="muted">{u.label}</span>
                      </li>
                    ))}
                  </ul>
                  <span className="muted">
                    password <code>demo-password</code> (typed on the IdP page only)
                  </span>
                </div>
              )}
            </>
          }
        </div>
      )}
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { hasAnyRole } = useAuth();
  const location = useLocation();
  const app = currentApp(location.pathname);
  const [navOpen, setNavOpen] = useState(false);
  const [lastPath, setLastPath] = useState(location.pathname);
  if (lastPath !== location.pathname) {
    setLastPath(location.pathname);
    setNavOpen(false);
  }

  return (
    <div className={`shell ${navOpen ? "nav-open" : ""}`}>
      <aside className="sidebar" aria-label="Primary">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            ◆
          </span>
          <span className="brand-name">Internal Tools</span>
        </div>
        <AppSwitcher active={app} />
        <nav className="nav">
          <div className="nav-label">{app.name}</div>
          {app.nav
            .filter((n) => !n.roles || hasAnyRole(...n.roles))
            .map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
              >
                <span className="nav-icon" aria-hidden>
                  {n.icon}
                </span>
                {n.label}
              </NavLink>
            ))}
          <div className="nav-label">Coming soon</div>
          {APPS.filter((a) => a.status === "planned").map((a) => (
            <span key={a.id} className="nav-item disabled" aria-disabled="true">
              <span className="nav-icon" aria-hidden>
                {a.icon}
              </span>
              {a.name}
            </span>
          ))}
        </nav>
        <div className="sidebar-foot">
          <Chip tone="success">Signed in via OIDC</Chip>
          <span className="muted small">Access scoped by business unit</span>
        </div>
      </aside>
      <div className="sidebar-scrim" onClick={() => setNavOpen(false)} aria-hidden />
      <div className="main-col">
        <header className="topbar">
          <button
            type="button"
            className="btn-icon nav-toggle"
            aria-label="Toggle navigation"
            onClick={() => setNavOpen((o) => !o)}
          >
            ☰
          </button>
          <div className="topbar-spacer" />
          <UserMenu />
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2 className="card-title">{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </div>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}
