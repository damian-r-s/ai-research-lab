import { type FormEvent, useState } from "react";

import { useCreateUser, useUsers } from "../api/hooks";
import type { Role } from "../api/types";
import { Card, ErrorNotice, Loading, Pill } from "../components/ui";
import { formatTime } from "../lib/format";

const ROLES: Role[] = ["viewer", "researcher", "admin"];

export function UsersPage() {
  const users = useUsers(true);
  const create = useCreateUser();
  const [form, setForm] = useState({ username: "", password: "", role: "viewer" as Role });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(form, { onSuccess: () => setForm({ username: "", password: "", role: "viewer" }) });
  };

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="lg:col-span-2">
        <Card title="Users">
          {users.isPending && <Loading />}
          {users.error && <ErrorNotice error={users.error} />}
          {users.data && (
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-slate-500">
                <tr>
                  <th className="py-1">Username</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th className="hidden sm:table-cell">Last login</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {users.data.map((u) => (
                  <tr key={u.id}>
                    <td className="py-2">{u.username}</td>
                    <td>{u.role}</td>
                    <td><Pill tone={u.is_active ? "good" : "neutral"}>{u.is_active ? "Active" : "Disabled"}</Pill></td>
                    <td className="hidden sm:table-cell">{u.last_login_at ? formatTime(u.last_login_at) : "Never"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
      <Card title="Add user">
        <form onSubmit={submit} className="space-y-3 text-sm">
          {create.error && <ErrorNotice error={create.error} />}
          {create.isSuccess && <p role="status" className="text-emerald-700 dark:text-emerald-400">User created.</p>}
          <label className="block space-y-1">
            <span>Username</span>
            <input className="w-full rounded-md border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
          </label>
          <label className="block space-y-1">
            <span>Password (at least 12 characters)</span>
            <input type="password" autoComplete="new-password" className="w-full rounded-md border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
          </label>
          <label className="block space-y-1">
            <span>Role</span>
            <select className="w-full rounded-md border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <button type="submit" disabled={create.isPending} className="w-full rounded-md bg-slate-900 px-3 py-2 font-medium text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900">
            {create.isPending ? "Creating…" : "Create user"}
          </button>
        </form>
      </Card>
    </div>
  );
}
