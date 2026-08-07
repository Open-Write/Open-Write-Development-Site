import { useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import Layout from "../components/Layout";

interface ApprovedEmail {
  email: string;
  is_admin: boolean;
  added_by: string | null;
  created_at: string | null;
}

export default function Admin() {
  const { user } = useAuth();
  const [emails, setEmails] = useState<ApprovedEmail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newIsAdmin, setNewIsAdmin] = useState(false);
  const [adding, setAdding] = useState(false);
  const [status, setStatus] = useState("");

  const load = () => {
    setLoading(true);
    api
      .listApprovedEmails()
      .then(setEmails)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newEmail.trim()) return;
    setAdding(true);
    setError("");
    setStatus("");
    try {
      await api.addApprovedEmail(newEmail.trim(), newIsAdmin);
      setNewEmail("");
      setNewIsAdmin(false);
      setStatus("Email added.");
      load();
    } catch (err) {
      setError((err as Error).message || "Failed to add email.");
    } finally {
      setAdding(false);
    }
  };

  const remove = async (email: string) => {
    if (email === user?.email) return;
    setError("");
    setStatus("");
    try {
      await api.removeApprovedEmail(email);
      setStatus(`${email} removed.`);
      load();
    } catch (err) {
      setError((err as Error).message || "Failed to remove email.");
    }
  };

  if (!user?.is_admin) {
    return (
      <Layout>
        <p className="text-gray-400">Admin access required.</p>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="mx-auto max-w-2xl space-y-8">
        <div>
          <h1 className="text-2xl font-semibold text-gray-100">
            Beta Access Management
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Manage who can create accounts during the beta period.
          </p>
        </div>

        {(error || status) && (
          <div className="space-y-2">
            {error && (
              <div className="rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">
                {error}
              </div>
            )}
            {status && (
              <div className="rounded-lg bg-green-600/15 px-3 py-2 text-sm text-green-300">
                {status}
              </div>
            )}
          </div>
        )}

        <form onSubmit={add} className="card space-y-4 p-6">
          <h2 className="text-lg font-semibold text-gray-200">
            Add approved email
          </h2>
          <div className="flex gap-3">
            <input
              className="input flex-1"
              type="email"
              required
              placeholder="user@example.com"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
            />
            <button className="btn-primary" disabled={adding}>
              {adding ? "Adding…" : "Add"}
            </button>
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-400">
            <input
              type="checkbox"
              checked={newIsAdmin}
              onChange={(e) => setNewIsAdmin(e.target.checked)}
              className="rounded border-edge"
            />
            Grant admin privileges
          </label>
        </form>

        <div className="card p-6">
          <h2 className="mb-4 text-lg font-semibold text-gray-200">
            Approved emails
          </h2>
          {loading ? (
            <p className="text-sm text-gray-500">Loading…</p>
          ) : emails.length === 0 ? (
            <p className="text-sm text-gray-500">No approved emails.</p>
          ) : (
            <div className="divide-y divide-edge">
              {emails.map((e) => (
                <div
                  key={e.email}
                  className="flex items-center justify-between py-3"
                >
                  <div>
                    <span className="text-sm text-gray-200">{e.email}</span>
                    {e.is_admin && (
                      <span className="ml-2 rounded bg-accent/20 px-1.5 py-0.5 text-xs text-accent">
                        admin
                      </span>
                    )}
                    {e.added_by && (
                      <span className="ml-2 text-xs text-gray-500">
                        added by {e.added_by}
                      </span>
                    )}
                  </div>
                  {e.email !== user?.email && (
                    <button
                      className="btn-ghost !py-1 text-xs text-red-400 hover:text-red-300"
                      onClick={() => remove(e.email)}
                    >
                      Remove
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
}
