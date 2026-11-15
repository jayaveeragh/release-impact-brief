import { useEffect, useState } from "react";
import { api, factId, moduleOf, routes, sourceUrl, type ModuleFlows, type NeedsConfirmation,
  type View } from "./api";

// One module, Level 2: what it is for, its endpoints (into their branch maps), classes,
// dependencies, and - on request - the flows the LLM picks, every step checked.
export function ModulePage({ name, module }: { name: string; module: string }) {
  const [view, setView] = useState<View | null>(null);
  const [flows, setFlows] = useState<ModuleFlows | NeedsConfirmation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.view(name).then(setView).catch((e: Error) => setError(e.message)); }, [name]);

  if (error) return <main className="page"><p className="error">{error}</p></main>;
  if (!view) return <main className="page"><p className="muted">Loading…</p></main>;
  const m = view.panels.modules.find((x) => x.id === module);
  if (!m) return <main className="page"><p className="error">No such module in this view.</p></main>;
  const link = (file: string, line?: number | null) => sourceUrl(view.repo, view.commit, file, line);
  const dirs = view.panels.modules.map((x) => x.id);
  const endpoints = view.panels.endpoints.filter((e) => moduleOf(e.file, dirs) === module);
  const entities = view.panels.entities.filter((e) => moduleOf(e.file, dirs) === module);
  const service = view.panels.services.find((s) => s.id === m.service);

  const ask = async (confirm: boolean) => {
    setBusy(true);
    setError(null);
    try {
      setFlows(await api.flows(name, module, confirm));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page">
      <p className="crumbs"><a href={routes.view(name)}>{view.repo.replace("https://github.com/", "")}</a> › module</p>
      <h1>{m.name}</h1>
      <p className="muted">{m.build_tools.join(", ")} · <a href={link(m.file)} target="_blank" rel="noreferrer">{m.file}</a>
        {service && <> · runs as <strong>{service.name}</strong>{service.port && <> on port {service.port}</>}</>}</p>
      {m.summary && <p className="summary">{m.summary}</p>}
      {m.capabilities.length > 0 && <ul>{m.capabilities.map((c, i) => <li key={i}>{c}</li>)}</ul>}
      {m.depends_on.length > 0 && <p>Depends on: {m.depends_on.map((d) => (
        <a key={d} className="cite" href={routes.module(name, d)}>{d.replace(/^build:module:/, "")}</a>))}</p>}

      <h2>Main flows</h2>
      {!flows && <button className="primary" disabled={busy} onClick={() => ask(false)}>
        {busy ? "Choosing flows… (a local model takes a minute or two)" : "Show the main flows"}</button>}
      {flows && "needs_confirmation" in flows && (
        <div className="confirm">
          <p>This model costs money: at least ${flows.estimate.min_usd.toFixed(3)}, at most ${flows.estimate.max_usd.toFixed(3)}.</p>
          <button disabled={busy} onClick={() => ask(true)}>Spend it</button>
        </div>
      )}
      {flows && "flows" in flows && (
        flows.flows.length === 0 ? <p className="muted">The model proposed no flows that survived validation.</p> : (
          flows.flows.map((f, i) => (
            <article key={i} className="flow">
              <h3>{f.title}</h3>
              <p className="muted">{f.description}</p>
              <ol>{f.steps.map((step, j) => {
                const id = factId(step);
                const fact = flows.facts[id];
                const label = fact?.name ?? id;
                return <li key={j}>{fact?.kind === "endpoint"
                  ? <a className="cite" href={routes.endpoint(name, id)}>{label}</a>
                  : <a className="cite" href={link(fact?.file ?? "", fact?.line)} target="_blank" rel="noreferrer">
                    {id.replace(/^java:method:/, "")}</a>}</li>;
              })}</ol>
            </article>
          ))
        )
      )}
      {flows && "removed" in flows && flows.removed.length > 0 && (
        <p className="muted small">{flows.removed.length} reference(s) removed by the validator.</p>)}

      <h2>Endpoints <span className="count">{endpoints.length}</span></h2>
      {endpoints.length === 0 ? <p className="muted">None.</p> : (
        <ul className="plain">{endpoints.map((e) => (
          <li key={e.id}><a href={routes.endpoint(name, e.id)}><span className={`method m-${e.method}`}>{e.method}</span>{" "}
            <code>{e.path}</code></a> <span className="muted small">{e.branches} branch points</span></li>))}</ul>
      )}

      {entities.length > 0 && <><h2>Entities</h2><p>{entities.map((e) => (
        <a key={e.id} className="cite" href={link(e.file, e.line)} target="_blank" rel="noreferrer">{e.table}</a>))}</p></>}

      <h2>Classes <span className="count">{m.classes.length}</span></h2>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Class</th><th>Role</th><th>What it does</th></tr></thead>
        <tbody>{m.classes.map((c) => (
          <tr key={c.id}>
            <td>{c.file ? <a href={link(c.file, c.line)} target="_blank" rel="noreferrer">{c.name}</a> : c.name}</td>
            <td>{c.role ?? "-"}</td>
            <td>{c.summary ?? <span className="muted">no summary ({c.error ?? "not asked"})</span>}</td>
          </tr>))}</tbody>
      </table></div>
    </main>
  );
}
