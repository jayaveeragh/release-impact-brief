import { useEffect, useMemo, useState } from "react";
import {
  api, routes, sourceUrl, type Brief, type Bug, type Candidate, type ImpactItem,
  type NeedsConfirmation, type View,
} from "./api";
import { Diagram } from "./Diagram";

/** "org.example.owner.OwnerController#show(int)" -> "OwnerController#show(int)" */
const brief_ = (label: string) => label.replace(/^([a-z0-9_]+\.)+(?=[A-Z])/, "");

// Release impact brief: describe a bug, confirm where it lives, get one page that says
// what a fix could affect and why. No probabilities; the repo's tests are never run.
export function ImpactPage({ name }: { name: string }) {
  const [view, setView] = useState<View | null>(null);
  const [bug, setBug] = useState<Bug>({ description: "", area: "", fix: "" });
  const [shortlist, setShortlist] = useState<Candidate[] | null>(null);
  const [chosen, setChosen] = useState<Map<string, Candidate>>(new Map());
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<Candidate[]>([]);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [pending, setPending] = useState<{ step: "candidates" | "brief"; ask: NeedsConfirmation } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.view(name).then(setView).catch((e: Error) => setError(e.message)); }, [name]);
  useEffect(() => {
    if (query.trim().length < 2) { setFound([]); return; }
    const t = window.setTimeout(() => api.search(name, query).then(setFound).catch(() => setFound([])), 250);
    return () => window.clearTimeout(t);
  }, [name, query]);

  const run = async <T,>(label: string, f: () => Promise<T>) => {
    setBusy(label); setError(null);
    try { return await f(); } catch (e) { setError((e as Error).message); return null; } finally { setBusy(null); }
  };
  const findCandidates = async (confirm = false) => {
    const result = await run("Finding where the bug likely lives…", () => api.candidates(name, bug, confirm));
    if (!result) return;
    if ("needs_confirmation" in result) { setPending({ step: "candidates", ask: result }); return; }
    setPending(null);
    setShortlist(result.shortlist);
    setChosen(new Map(result.picked.map((c) => [c.id, c])));
    setBrief(null);
  };
  const makeBrief = async (confirm = false) => {
    const result = await run("Tracing what depends on these locations…",
      () => api.brief(name, bug, [...chosen.keys()], confirm));
    if (!result) return;
    if ("needs_confirmation" in result) { setPending({ step: "brief", ask: result }); return; }
    setPending(null);
    setBrief(result);
  };
  const toggle = (c: Candidate) => setChosen((m) => {
    const next = new Map(m);
    if (next.has(c.id)) next.delete(c.id); else next.set(c.id, c);
    return next;
  });

  if (error && !view) return <main className="page"><p className="error">{error}</p></main>;
  if (!view) return <main className="page"><p className="muted">Loading…</p></main>;
  const link = (file: string | null, line: number | null) => file ? sourceUrl(view.repo, view.commit, file, line) : undefined;

  return (
    <main className="page impact-page">
      <p className="crumbs no-print"><a href={routes.view(name)}>{view.repo.replace("https://github.com/", "")}</a> › release impact brief</p>
      {!brief && (
        <>
          <h1>Release impact brief</h1>
          <p className="muted">Describe the bug; rough is fine. You'll confirm where it lives before anything is traced.</p>
          <form className="bug-form" onSubmit={(e) => { e.preventDefault(); findCandidates(); }}>
            <label>Bug<textarea required rows={3} value={bug.description}
              onChange={(e) => setBug({ ...bug, description: e.target.value })}
              placeholder="e.g. Searching owners returns the wrong people when the name has trailing spaces" /></label>
            <label>Suspected area (optional)<input value={bug.area} onChange={(e) => setBug({ ...bug, area: e.target.value })} /></label>
            <label>Likely fix (optional)<input value={bug.fix} onChange={(e) => setBug({ ...bug, fix: e.target.value })} /></label>
            <button className="primary" disabled={!!busy || !bug.description.trim()}>Find likely locations</button>
          </form>
        </>
      )}
      {busy && <p className="muted">{busy} (a local model takes up to a minute)</p>}
      {error && <p className="error">{error}</p>}
      {pending && (
        <div className="confirm">
          <p>This model costs money: up to ${pending.ask.estimate.max_usd.toFixed(3)}.</p>
          <button onClick={() => pending.step === "candidates" ? findCandidates(true) : makeBrief(true)}>Spend it</button>
        </div>
      )}

      {shortlist && !brief && (
        <section>
          <h2>Confirm the locations</h2>
          <p className="muted">The analysis follows these, so check them. Ticked ones were picked by the model, with its reason.</p>
          <ul className="locations">
            {[...new Map([...chosen.values(), ...shortlist].map((c) => [c.id, c])).values()].map((c) => (
              <li key={c.id}><label>
                <input type="checkbox" checked={chosen.has(c.id)} onChange={() => toggle(c)} />
                <span className="tag">{c.kind}</span> <code title={c.label}>{brief_(c.label)}</code>
                {chosen.get(c.id)?.reason && <span className="muted"> — {chosen.get(c.id)!.reason}</span>}
                {c.file && <> <a className="src" href={link(c.file, c.line)} target="_blank" rel="noreferrer">{c.file.split("/").pop()}:{c.line}</a></>}
              </label></li>))}
          </ul>
          <input className="search" placeholder="Add another: search by class, method, field or endpoint name"
            value={query} onChange={(e) => setQuery(e.target.value)} />
          {found.length > 0 && <ul className="locations found">{found.map((c) => (
            <li key={c.id}><button type="button" className="link" onClick={() => { toggle(c); setQuery(""); }}>
              + <span className="tag">{c.kind}</span> <code title={c.label}>{brief_(c.label)}</code></button></li>))}</ul>}
          <button className="primary" disabled={!!busy || chosen.size === 0} onClick={() => makeBrief()}>
            Build the brief from {chosen.size} location{chosen.size === 1 ? "" : "s"}</button>
        </section>
      )}

      {brief && <BriefView name={name} view={view} brief={brief} link={link} onEdit={() => setBrief(null)} />}
    </main>
  );
}

function BriefView({ name, view, brief, link, onEdit }: {
  name: string; view: View; brief: Brief;
  link: (file: string | null, line: number | null) => string | undefined; onEdit: () => void;
}) {
  const highlight = useMemo(() => new Set(brief.highlight), [brief]);
  const refs = useMemo(() => new Map([...brief.high, ...brief.low, ...brief.tests].map((i) => [i.ref, i])), [brief]);
  const diagram = view.sections.find((s) => s.type === "architecture_diagram");
  const download = () => {
    const url = URL.createObjectURL(new Blob([brief.markdown], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url; a.download = `impact-brief-${name}.md`; a.click();
    URL.revokeObjectURL(url);
  };
  const Cites = ({ cites }: { cites: string[] }) => <>{cites.map((c) => {
    const item = refs.get(c);
    return item ? <a key={c} className="cite" href={link(item.file, item.line)} target="_blank" rel="noreferrer" title={item.reason}>{c}</a> : null;
  })}</>;
  const Items = ({ items }: { items: ImpactItem[] }) => items.length === 0 ? <p className="muted">None found.</p> : (
    <ul className="impact-items">{items.map((i) => (
      <li key={i.ref}><span className="ref">{i.ref}</span> <span className="tag">{i.item}</span>{" "}
        {i.item === "endpoint" ? <a href={routes.endpoint(name, i.id)}><code>{i.label}</code></a> : <code title={i.label}>{brief_(i.label)}</code>}
        {i.condition && <code className="muted small"> {i.condition}</code>}
        <div className="muted small">{i.reason}{i.confidence && i.confidence !== "exact" && <> · <span className={`conf-${i.confidence}`}>{i.confidence}</span></>}
          {i.file && <> · <a className="src" href={link(i.file, i.line)} target="_blank" rel="noreferrer">{i.file.split("/").pop()}:{i.line}</a></>}
          {i.path && i.path.length > 1 && <> · path: {i.path.map(brief_).join(" → ")}</>}</div>
      </li>))}</ul>
  );

  return (
    <article className="brief">
      <div className="no-print actions">
        <button className="secondary" onClick={onEdit}>← Change locations</button>
        <button className="secondary" onClick={() => window.print()}>Print / PDF</button>
        <button className="secondary" onClick={download}>Download Markdown</button>
      </div>
      <h1>Release impact brief</h1>
      <p className="muted">{view.repo.replace("https://github.com/", "")} @ {view.commit.slice(0, 7)}</p>
      <p className="bug">{brief.bug.split("\n").map((l, i) => <span key={i}>{l}<br /></span>)}</p>
      <p className="muted small">Locations: {brief.locations.map((l) => brief_(l.label)).join(", ")}</p>

      <section className={`size size-${brief.size.value.replace(/[^a-z]+/g, "-")}`}>
        <h2>Size: {brief.size.value}</h2>
        <ul>{brief.size.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
      </section>

      {brief.explanation.summary.length > 0 && (
        <section><h2>In short</h2><ul>{brief.explanation.summary.map((s, i) => (
          <li key={i}>{s.text} <Cites cites={s.cites} /></li>))}</ul></section>)}
      {brief.explanation.error && <p className="muted small">No explanation: {brief.explanation.error}</p>}

      <div className="two-col">
        <section><h2>High impact <span className="count">{brief.high.length}</span></h2><Items items={brief.high} /></section>
        <section><h2>Low impact <span className="count">{brief.low.length}</span></h2><Items items={brief.low} /></section>
      </div>

      <section>
        <h2>What to test</h2>
        {brief.explanation.what_to_test.length > 0 && <ul>{brief.explanation.what_to_test.map((s, i) => (
          <li key={i}>{s.text} <Cites cites={s.cites} /></li>))}</ul>}
        <Items items={brief.tests} />
      </section>

      <section className="unseen">
        <h2>What the tool couldn't see</h2>
        <ul>{brief.couldnt_see.map((n, i) => <li key={i}>{n}</li>)}</ul>
      </section>

      {diagram?.type === "architecture_diagram" && (
        <section className="overlay"><h2>Impact map</h2>
          <p className="muted small">Affected boxes are highlighted; the rest are dimmed.</p>
          <Diagram section={diagram} facts={view.facts} highlight={highlight} onSelect={() => undefined} /></section>)}
      <p className="muted small">No probabilities: this lists what depends on the change and why. The repository's tests were not run.</p>
    </article>
  );
}
