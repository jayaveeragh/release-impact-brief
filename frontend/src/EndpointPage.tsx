import { useEffect, useState } from "react";
import { ChatPanel } from "./ChatPanel";
import { api, routes, sourceUrl, type Branch, type BranchMap, type InputRow, type View } from "./api";

// One endpoint, Level 3: its inputs, which of them drive which branches, and where each
// branch goes. Computed from facts when the page opens; no LLM involved.
export function EndpointPage({ name, endpoint }: { name: string; endpoint: string }) {
  const [view, setView] = useState<View | null>(null);
  const [map, setMap] = useState<BranchMap | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    Promise.all([api.view(name), api.branches(name, endpoint)])
      .then(([v, m]) => { setView(v); setMap(m); })
      .catch((e: Error) => setError(e.message));
  }, [name, endpoint]);

  if (error) return <main className="page"><p className="error">{error}</p></main>;
  if (!view || !map) return <main className="page"><p className="muted">Working out the branches…</p></main>;
  const link = (line: number, file = map.file) => sourceUrl(view.repo, view.commit, file, line);
  const L = ({ line }: { line: number }) => (
    <a className="src" href={link(line)} target="_blank" rel="noreferrer">L{line}</a>
  );
  const drivers = map.inputs.filter((r) => r.role === "drives");
  const depth = (b: Branch) => {
    let d = 0;
    for (let p = b.parent; p !== null; p = map.branches[p].parent) d++;
    return d;
  };

  return (
    <main className="page endpoint-page">
      <p className="crumbs"><a href={routes.view(name)}>{view.repo.replace("https://github.com/", "")}</a> › endpoint</p>
      <h1><span className={`method m-${map.name.split(" ")[0]}`}>{map.name.split(" ")[0]}</span>{" "}
        <code>{map.name.split(" ").slice(1).join(" ")}</code></h1>
      <p className="muted">handled by <a href={link(map.line)} target="_blank" rel="noreferrer">
        {map.handler}</a> · {map.branches.length} branch points · {drivers.length} of{" "}
        {map.inputs.length} inputs drive them · returns at {map.returns.length} places
        {map.throws.length > 0 && <>, throws at {map.throws.length}</>}</p>

      {(Object.keys(map.validation).length > 0 || map.custom_validator) && (
        <section className="note-box">
          {Object.entries(map.validation).map(([input, fields]) => (
            <p key={input}><strong>validation:{input}</strong> checks {fields.length > 0
              ? fields.join(", ") : "no fields with constraints"} (from the fields' declared constraints).</p>))}
          {map.custom_validator && <p>A custom validator, <strong>{map.custom_validator}</strong>, is
            installed with <code>@InitBinder</code>; the fields it checks are not traced.</p>}
        </section>
      )}

      <h2>Which inputs drive which branches</h2>
      <p className="muted small">● drives the branch · ○ the branch reads the whole object, so this field may matter</p>
      {map.branches.length === 0 ? <p className="muted">This handler has no branches: every request takes the same path.</p> : (
        <div className="table-wrap"><table className="matrix">
          <thead><tr><th>Input</th><th>Role</th>{map.branches.map((b, i) => (
            <th key={i} title={b.condition || b.exception}>{b.kind}<br /><L line={b.line} /></th>))}</tr></thead>
          <tbody>
            {map.inputs.map((row) => (
              <tr key={row.name} className={`role-${row.role.replaceAll(" ", "-")}`}>
                <td><code>{row.name}</code></td>
                <td><RoleBadge row={row} /></td>
                {map.branches.map((b, i) => (
                  <td key={i} className="cell">{!row.lines.includes(b.line) ? ""
                    : row.role === "read as a whole" ? "○" : "●"}</td>))}
              </tr>
            ))}
          </tbody>
        </table></div>
      )}

      <h2>Branch map</h2>
      <ol className="branches">
        {map.branches.map((b, i) => (
          <li key={i} style={{ marginLeft: `${depth(b) * 1.5}rem` }} className={`branch kind-${b.kind}`}>
            <div className="branch-head">
              <span className="tag">{b.kind}</span> <L line={b.line} />{" "}
              {b.kind === "catch" ? <code>catch ({b.exception})</code> : <code>{b.condition}</code>}
            </div>
            <div className="branch-body">
              <span className="muted">driven by </span>
              {b.drivers.length > 0 ? b.drivers.map((d) => <span key={d} className="driver">{d}</span>)
                : <span className="muted">no request input ({b.kind === "catch" ? "an exception" : "internal state"})</span>}
              {b.exits.length > 0 && <div><span className="muted">{b.kind === "if" ? "when true, leaves at " : "leaves at "}</span>
                {b.exits.map((l) => <span key={l} className="exit">{map.throws.includes(l) ? "throw " : "return "}<L line={l} /></span>)}</div>}
              {b.else_exits && b.else_exits.length > 0 && <div><span className="muted">otherwise leaves at </span>
                {b.else_exits.map((l) => <span key={l} className="exit">{map.throws.includes(l) ? "throw " : "return "}<L line={l} /></span>)}</div>}
              {b.cases && b.cases.length > 0 && <ul className="cases">{b.cases.map((c, j) => (
                <li key={j}><code>{c.label}</code> <L line={c.line} />{c.exits.length > 0 && <> → leaves at {c.exits.map((l) => <L key={l} line={l} />)}</>}</li>))}</ul>}
              {b.calls.length > 0 && <div className="muted small">calls {b.calls.join(", ")}</div>}
            </div>
          </li>
        ))}
      </ol>
      <p className="muted">Exits outside any branch: {map.returns.filter((l) => !map.branches.some((b) => b.exits.includes(l) || b.else_exits?.includes(l)))
        .map((l) => <span key={l} className="exit">return <L line={l} /></span>)}</p>

      <ChatPanel name={name} repo={view.repo} commit={view.commit}
        context={{ kind: "endpoint", id: endpoint }} />

      <h2>Parameters</h2>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Input</th><th>Type</th><th>From</th><th>Constraints</th><th>Role</th><th>Branches</th></tr></thead>
        <tbody>{map.inputs.map((row) => (
          <tr key={row.name}>
            <td><code>{row.name}</code>{row.implicit && <span className="tag">implicit</span>}</td>
            <td><code>{row.type}</code></td>
            <td>{row.source}</td>
            <td>{(row.constraints ?? []).join(", ")}{row.validated && "validated"}</td>
            <td><RoleBadge row={row} /></td>
            <td>{row.lines.map((l) => <L key={l} line={l} />)}</td>
          </tr>))}</tbody>
      </table></div>
      {(map.unresolved_calls.length > 0) && <p className="muted small">Calls that couldn't be resolved: {map.unresolved_calls.join(", ")}</p>}
    </main>
  );
}

function RoleBadge({ row }: { row: InputRow }) {
  return <span className={`role role-${row.role.replaceAll(" ", "-")}`}
    title={row.role === "read as a whole" ? "a branch reads the whole object; which fields isn't traced" : undefined}>
    {row.role}</span>;
}
