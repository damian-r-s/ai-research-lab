import { Link, Route, Routes } from "react-router";

import { Layout } from "./components/Layout";
import { AuditLogPage } from "./pages/AuditLogPage";
import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
import { UsersPage } from "./pages/UsersPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="audit" element={<AuditLogPage />} />
      </Route>
      <Route element={<Layout minRole="admin" />}>
        <Route path="users" element={<UsersPage />} />
      </Route>
      <Route
        path="*"
        element={
          <main className="p-6">
            <p>Page not found.</p>
            <Link className="underline" to="/">Back to the dashboard</Link>
          </main>
        }
      />
    </Routes>
  );
}
