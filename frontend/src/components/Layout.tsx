import { type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth";
import HelpWidget from "./HelpWidget";

// App shell with top navigation. Used by every authenticated page.
export default function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const loc = useLocation();
  const onSettings = loc.pathname.startsWith("/settings");
  const onAdmin = loc.pathname.startsWith("/admin");
  const onEditorial = loc.pathname.startsWith("/editorial");

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-edge bg-ink-950/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-6 py-3">
          <Link to="/" className="text-lg font-semibold text-gray-100">Open-Write</Link>
          <nav className="ml-4 flex items-center gap-1 text-sm">
            <Link
              to="/"
              className={`rounded-lg px-3 py-1.5 ${!onSettings && !onAdmin && !onEditorial ? "bg-ink-800 text-gray-100" : "text-gray-400 hover:text-gray-200"}`}
            >
              Projects
            </Link>
            <Link
              to="/settings"
              className={`rounded-lg px-3 py-1.5 ${onSettings ? "bg-ink-800 text-gray-100" : "text-gray-400 hover:text-gray-200"}`}
            >
              Settings
            </Link>
            <Link
              to="/editorial"
              className={`rounded-lg px-3 py-1.5 ${onEditorial ? "bg-ink-800 text-gray-100" : "text-gray-400 hover:text-gray-200"}`}
            >
              Editorial
            </Link>
            {user?.is_admin && (
              <Link
                to="/admin"
                className={`rounded-lg px-3 py-1.5 ${onAdmin ? "bg-ink-800 text-gray-100" : "text-gray-400 hover:text-gray-200"}`}
              >
                Admin
              </Link>
            )}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <a href="/" className="btn-ghost !py-1.5 text-gray-400 hover:text-gray-200" title="Exit to Open-Write Studio home page">
              Exit
            </a>
            <span className="text-gray-500">{user?.email}</span>
            <button className="btn-ghost !py-1.5" onClick={logout}>Log out</button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-6">{children}</main>
      <HelpWidget />
    </div>
  );
}
