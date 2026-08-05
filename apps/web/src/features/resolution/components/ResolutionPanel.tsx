import { useState } from "react";

import type { ResolutionResult } from "../../../types/contracts";
import type { ResolutionPhase } from "../hooks/use-resolution";
import "../styles/resolution.css";

export function ResolutionPanel({
  phase,
  statusMessage,
  error,
  result,
  onCancel,
}: {
  phase: ResolutionPhase;
  statusMessage: string;
  error: string;
  result: ResolutionResult | null;
  onCancel: () => void;
}) {
  if (phase === "idle") return null;
  const running = phase === "submitting" || phase === "running";
  const deliveryLinks = result?.deliveryLinks.filter((link) => safeDeliveryURL(link.url) !== null) ?? [];

  return (
    <section className={`resolution-panel resolution-${phase}`} aria-label="Link resolution">
      {running && (
        <div className="resolution-running" role="status" aria-live="polite">
          <span className="resolution-spinner" aria-hidden="true" />
          <p>{statusMessage || "Checking links…"}</p>
          <button type="button" className="resolution-cancel" onClick={onCancel}>Cancel</button>
        </div>
      )}

      {(phase === "verified" || phase === "partial") && (
        <div className="delivery-links" aria-live="polite">
          <div className="delivery-links-heading">
            <div>
              <p className="eyebrow">Verified</p>
              <h3>Delivery Links</h3>
            </div>
            <p>{statusMessage}</p>
          </div>
          {deliveryLinks.length ? (
            <ul className="delivery-link-list">
              {deliveryLinks.map((link, index) => (
                <DeliveryLinkItem key={`${link.sourceId}-${link.url}-${index}`} link={link} />
              ))}
            </ul>
          ) : (
            <p className="resolution-message" role="alert">The server result contained no safe delivery URL.</p>
          )}
        </div>
      )}

      {(phase === "blocked" || phase === "failed" || phase === "error") && (
        <div className="resolution-message resolution-error" role="alert">
          <strong>{phase === "blocked" ? "Links blocked" : "Links unavailable"}</strong>
          <p>{error || statusMessage || "No verified delivery link was found."}</p>
        </div>
      )}

      {phase === "cancelled" && (
        <p className="resolution-message" role="status">{statusMessage || "Link request cancelled."}</p>
      )}
    </section>
  );
}

function DeliveryLinkItem({ link }: { link: ResolutionResult["deliveryLinks"][number] }) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const safeURL = safeDeliveryURL(link.url);
  if (!safeURL) return null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(safeURL);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  };

  return (
    <li className="delivery-link-item">
      <div className="delivery-link-details">
        <strong>{link.filename}</strong>
        <span>{[link.quality, link.size].filter(Boolean).join(" · ")}</span>
        <span>Verified {formatVerifiedTime(link.verifiedAt)}</span>
      </div>
      <div className="delivery-link-actions">
        <a
          className="delivery-open-link"
          href={safeURL}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open / Download
        </a>
        <button type="button" className="delivery-copy-link" onClick={() => void copy()}>
          {copyState === "copied" ? "Copied" : "Copy Link"}
        </button>
      </div>
      {copyState === "failed" && <p className="delivery-copy-error" role="alert">Copy failed. Open the link and copy it from the browser.</p>}
    </li>
  );
}

function safeDeliveryURL(value: string): string | null {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

function formatVerifiedTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "recently";
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
