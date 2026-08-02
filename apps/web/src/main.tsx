import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import "./styles/globals.css";
import "./styles/layout.css";

const root = document.getElementById("root");
if (!root) throw new Error("React root element is missing");

createRoot(root).render(<App />);
