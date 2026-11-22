import { NavLink, Navigate, Outlet, useLocation } from "react-router";

import { useLogout, useMe } from "../api/hooks";
import type { Role } from "../api/types";
import { ErrorNotice, Loading } from "./ui";

const RANK: Record<Role, number> = { viewer: 0, researcher: 1, admin: 2 };

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm ${isActive ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "hover:bg-slate-200 dark:hover:bg-slate-800"}`;

/** Authenticated shell: redirects to /login without a session, hides pages above the user's role. */
export function Layout({ minRole = "viewer" }: { minRole?: Role }) {
  const me = useMe();
  const logout = useLogout();
  const location = useLocation();

  if (me.isPending) return <Loading />;
  if (me.error) return <main className="p-4"><ErrorNotice error={me.error} /></main>;
  if (!me.data) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  const user = me.data;

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-2 px-4 py-2">
          <span className="mr-4 font-semibold">AI Research Lab</span>
          <nav className="flex flex-wrap gap-1" aria-label="Main">
            <NavLink to="/" end className={linkClass}>Dashboard</NavLink>
            <NavLink to="/audit" className={linkClass}>Audit log</NavLink>
            {user.role === "admin" && <NavLink to="/users" className={linkClass}>Users</NavLink>}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-slate-500 dark:text-slate-400">{user.username} ({user.role})</span>
            <button
              type="button"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
              className="rounded-md border border-slate-300 px-3 py-1.5 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl p-4">
        {RANK[user.role] >= RANK[minRole] ? <Outlet /> : <ErrorNotice error={new Error(`This page requires the ${minRole} role.`)} />}
      </main>
    </div>
  );
}
