import { ExternalLink, Settings, ShieldCheck } from "lucide-react";
import { publicPath } from "../app/runtime-paths";
import "../features/library/styles/library.css";

export function AdminPlaceholderPage() {
  return (
    <section className="library-page" aria-label="Admin tools">
      <aside className="library-mode-notice" aria-label="Admin bridge status">
        <ShieldCheck aria-hidden="true" focusable="false" />
        <div>
          <strong>Live admin tools</strong>
          <p>Source management, Jellyfin setup, adapter maker, scans, and maintenance run through the live admin console.</p>
        </div>
      </aside>
      <div className="library-grid">
        <a className="library-card admin-link-card" href={publicPath("/legacy/admin")}>
          <div className="library-poster-fallback" aria-hidden="true"><Settings /></div>
          <div className="library-card-body">
            <div className="library-card-title-row">
              <div>
                <h2>Admin Dashboard</h2>
                <p className="library-card-meta">Library scans, matching, source health, and maintenance.</p>
              </div>
              <ExternalLink aria-hidden="true" focusable="false" />
            </div>
          </div>
        </a>
        <a className="library-card admin-link-card" href={publicPath("/legacy/admin/sources")}>
          <div className="library-poster-fallback" aria-hidden="true"><Settings /></div>
          <div className="library-card-body">
            <div className="library-card-title-row">
              <div>
                <h2>Sources</h2>
                <p className="library-card-meta">Add, test, enable, disable, or diagnose source adapters.</p>
              </div>
              <ExternalLink aria-hidden="true" focusable="false" />
            </div>
          </div>
        </a>
        <a className="library-card admin-link-card" href={publicPath("/legacy/setup")}>
          <div className="library-poster-fallback" aria-hidden="true"><Settings /></div>
          <div className="library-card-body">
            <div className="library-card-title-row">
              <div>
                <h2>Jellyfin Setup</h2>
                <p className="library-card-meta">Jellyfin connection, media folders, TMDB, and sync settings.</p>
              </div>
              <ExternalLink aria-hidden="true" focusable="false" />
            </div>
          </div>
        </a>
      </div>
    </section>
  );
}
