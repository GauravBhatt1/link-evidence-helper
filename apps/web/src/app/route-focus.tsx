import { useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { metadataForPath } from "./route-metadata";

export function RouteFocus() {
  const location = useLocation();
  const previousKey = useRef(location.key);

  useLayoutEffect(() => {
    document.title = metadataForPath(location.pathname).documentTitle;
    const navigatedInClient = previousKey.current !== location.key;
    previousKey.current = location.key;
    if (!navigatedInClient) return;

    const frame = window.requestAnimationFrame(() => {
      document.getElementById("route-heading")?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [location.key, location.pathname]);

  return null;
}
