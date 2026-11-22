import { type FormEvent, useState } from "react";
import { Navigate, useSearchParams } from "react-router";

import { useLogin, useMe } from "../api/hooks";
import { ErrorNotice } from "../components/ui";

export function LoginPage() {
  const me = useMe();
  const login = useLogin();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  // Only same-site paths: "//evil.example" or "https://…" would be an open redirect.
  const requested = params.get("next") ?? "/";
  const next = requested.startsWith("/") && !requested.startsWith("//") ? requested : "/";

  if (me.data) return <Navigate to={next} replace />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ username, password });
  };

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div>
          <h1 className="text-xl font-semibold">AI Research Lab</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Sign in to the control panel</p>
        </div>
        {login.error && <ErrorNotice error={login.error} />}
        <label className="block space-y-1 text-sm">
          <span>Username</span>
          <input
            className="w-full rounded-md border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span>Password</span>
          <input
            className="w-full rounded-md border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <button
          type="submit"
          disabled={login.isPending}
          className="w-full rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900"
        >
          {login.isPending ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
