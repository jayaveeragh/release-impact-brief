import { useEffect, useState } from "react";
import { Home } from "./Home";
import { ViewPage } from "./ViewPage";

// Tiny hash router: #/ is home, #/view/<name> is a saved view (so a reload keeps the page).
function route(): string | null {
  const match = /^#\/view\/(.+)$/.exec(window.location.hash);
  return match ? decodeURIComponent(match[1]) : null;
}

export function App() {
  const [view, setView] = useState<string | null>(route);
  useEffect(() => {
    const onHash = () => setView(route());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const open = (name: string | null) => {
    window.location.hash = name ? `#/view/${encodeURIComponent(name)}` : "#/";
  };
  return (
    <div className="app">
      <header className="topbar">
        <button className="brand" onClick={() => open(null)}>Release Impact Brief</button>
        <span className="tagline">Facts first, explanations on top</span>
      </header>
      {view ? <ViewPage key={view} name={view} /> : <Home onOpen={open} />}
    </div>
  );
}
