import { useEffect, useState } from "react";
import { EndpointPage } from "./EndpointPage";
import { Home } from "./Home";
import { ModulePage } from "./ModulePage";
import { ViewPage } from "./ViewPage";

// Hash routes (a reload keeps the page):
//   #/                                home
//   #/view/<name>                     a saved view
//   #/view/<name>/endpoint/<id>       one endpoint's branch map
//   #/view/<name>/module/<id>         one module
type Route =
  | { page: "home" }
  | { page: "view"; name: string }
  | { page: "endpoint" | "module"; name: string; id: string };

function route(): Route {
  const parts = window.location.hash.replace(/^#\/?/, "").split("/");
  if (parts[0] !== "view" || !parts[1]) return { page: "home" };
  const name = decodeURIComponent(parts[1]);
  if ((parts[2] === "endpoint" || parts[2] === "module") && parts[3]) {
    return { page: parts[2], name, id: decodeURIComponent(parts[3]) };
  }
  return { page: "view", name };
}

export function App() {
  const [current, setCurrent] = useState<Route>(route);
  useEffect(() => {
    const onHash = () => {
      setCurrent(route());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const open = (name: string | null) => {
    window.location.hash = name ? `#/view/${encodeURIComponent(name)}` : "#/";
  };
  const key = JSON.stringify(current);
  return (
    <div className="app">
      <header className="topbar">
        <button className="brand" onClick={() => open(null)}>Release Impact Brief</button>
        <span className="tagline">Facts first, explanations on top</span>
      </header>
      {current.page === "home" && <Home onOpen={open} />}
      {current.page === "view" && <ViewPage key={key} name={current.name} />}
      {current.page === "endpoint" && <EndpointPage key={key} name={current.name} endpoint={current.id} />}
      {current.page === "module" && <ModulePage key={key} name={current.name} module={current.id} />}
    </div>
  );
}
