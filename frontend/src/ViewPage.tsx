import { useEffect, useState } from "react";
import { api, factId, routes, sourceUrl, type FactInfo, type Section, type Verdict, type View } from "./api";
import { Diagram } from "./Diagram";
import { Panels } from "./Panels";

export function ViewPage({ name }: { name: string }) {
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    setView(null);
    api.view(name).then(setView).catch((e: Error) => setError(e.message));
  }, [name]);

  if (error) return <main className="page"><p className="error">{error}</p></main>;
  if (!view) return <main className="page"><p className="muted">Loading…</p></main>;
  const link = (file: string, line?: number | null) => sourceUrl(view.repo, view.commit, file, line);
  const diagram = view.sections.find((s) => s.type === "architecture_diagram");

  return (
    <main className="page">
      <section className="repo-head">
        <h1><a href={view.repo} target="_blank" rel="noreferrer">
          {view.repo.replace("https://github.com/", "")}</a></h1>
        <p className="muted">
          commit <a href={`${view.repo}/tree/${view.commit}`} target="_blank" rel="noreferrer">
            {view.commit.slice(0, 7)}</a> · explained by {view.model}
        </p>
        <div className="verdicts">
          <VerdictBadge label="Layout" verdict={view.repo_layout} link={link} />
          <VerdictBadge label="Runtime" verdict={view.runtime_style} link={link} />
        </div>
        {view.summary && <p className="summary">{view.summary.summary}</p>}
      </section>

      <div className="workspace">
        <div className="main-col">
          {diagram?.type === "architecture_diagram" ? (
            <>
              <h2>{diagram.title}</h2>
              <Diagram section={diagram} facts={view.facts} onSelect={setSelected} />
            </>
          ) : <p className="muted">No architecture diagram in this view.</p>}
          <Panels name={name} view={view} onSelect={setSelected} link={link} />
        </div>
        <aside className="side-col">
          <FactDetail id={selected} name={name} view={view} link={link} />
          {view.removed.length > 0 && (
            <details className="removed">
              <summary>{view.removed.length} reference{view.removed.length > 1 ? "s" : ""} removed
                by the validator</summary>
              <ul>{view.removed.map((r, i) => (
                <li key={i}><code>{String(r.ref)}</code>: {r.reason}</li>))}</ul>
            </details>
          )}
        </aside>
      </div>
    </main>
  );
}

function VerdictBadge({ label, verdict, link }: {
  label: string; verdict: Verdict; link: (f: string, l?: number | null) => string;
}) {
  return (
    <details className={`verdict verdict-${verdict.value.replace(/\s+/g, "-")}`}>
      <summary>{label}: <strong>{verdict.value}</strong></summary>
      <ul>{verdict.evidence.map((e, i) => (
        <li key={i}>{e.text}{e.file && <> · <a href={link(e.file, e.line)} target="_blank"
          rel="noreferrer">{e.file}{e.line ? `:${e.line}` : ""}</a></>}</li>))}</ul>
    </details>
  );
}

function FactDetail({ id, name, view, link }: {
  id: string | null; name: string; view: View; link: (f: string, l?: number | null) => string;
}) {
  if (!id) return <p className="muted hint">Click a box, link or citation to see where it comes from.</p>;
  const fact: FactInfo | undefined = view.facts[id];
  const flows = view.sections.filter((s): s is Extract<Section, { type: "flow" }> =>
    s.type === "flow" && s.steps.some((step) => factId(step) === id));
  return (
    <section className="fact-detail">
      <h3>{fact?.name ?? id}</h3>
      {fact ? (
        <>
          <p className="muted">{fact.type === "edge" ? `${fact.kind} link` : fact.kind.replace("_", " ")}
            {fact.confidence && <> · confidence <span className={`conf-${fact.confidence}`}>
              {fact.confidence}</span></>}</p>
          {fact.type === "edge" && <p>{view.facts[fact.src!]?.name ?? fact.src} → {view.facts[fact.dst!]?.name ?? fact.dst}</p>}
          <p><a href={link(fact.file, fact.line)} target="_blank" rel="noreferrer">
            {fact.file}:{fact.line}</a></p>
        </>
      ) : <p><code>{id}</code></p>}
      {flows.length > 0 && <p className="muted">In flows: {flows.map((f) => f.title).join(", ")}</p>}
      {fact?.kind === "endpoint" && <p><a href={routes.endpoint(name, id)}>Open its branch map →</a></p>}
      {fact?.kind === "module" && <p><a href={routes.module(name, id)}>Open the module →</a></p>}
      {fact?.kind === "service" && (() => {
        const module = view.panels.services.find((s) => s.id === id)?.module;
        return module ? <p><a href={routes.module(name, module)}>Open its module →</a></p> : null;
      })()}
    </section>
  );
}
