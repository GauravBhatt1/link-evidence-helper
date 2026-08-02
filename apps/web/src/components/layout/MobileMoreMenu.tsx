import { Ellipsis } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { routeMetadata } from "../../app/route-metadata";
import { NavItem } from "../ui/NavItem";

export function MobileMoreMenu() {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const disclosureId = useId();
  const location = useLocation();
  const routes = routeMetadata.filter((route) => !route.mobilePrimary);
  const moreIsActive = routes.some((route) => route.path === location.pathname);

  const dismissAndReturnFocus = () => {
    setOpen(false);
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  };

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dismissAndReturnFocus();
      }
    };
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) dismissAndReturnFocus();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  return (
    <div className="mobile-more" ref={containerRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`mobile-nav-action${moreIsActive ? " active" : ""}`}
        aria-expanded={open}
        aria-controls={disclosureId}
        onClick={() => setOpen((value) => !value)}
      >
        <Ellipsis aria-hidden="true" focusable="false" />
        <span>More</span>
      </button>
      {open && (
        <div id={disclosureId} className="more-disclosure" data-testid="mobile-more-disclosure">
          <nav aria-label="More navigation">
            {routes.map((route) => (
              <NavItem key={route.id} route={route} onNavigate={() => setOpen(false)} />
            ))}
          </nav>
        </div>
      )}
    </div>
  );
}
