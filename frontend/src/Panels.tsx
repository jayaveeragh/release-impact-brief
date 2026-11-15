import { useState } from "react";
import { factId, routes, type View } from "./api";

type Link = (file: string, line?: number | null) => string;
type Props = { name: string; view: View; onSelect: (id: string) => void; link: Link };

const TABS = ["Overview", "Services", "Modules", "Endpoints", "Data model", "Stores & outside",
  "Unclear"] as const;

export function Panels(props: Props) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Overview");
  const counts: Partial<Record<(typeof TABS)[number], number>> = {
    Services: props.view.panels.services.length,
    Modules: props.view.panels.modules.length,
    Endpoints: props.view.panels.endpoints.length,
    "Data model": props.view.panels.entities.length,
    "Stores & outside": props.view.panels.data_stores.length + props.view.panels.external_systems.length,
    Unclear: props.view.panels.unclear.length,
  };
  return (
    <section className="panels">
      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""}
            onClick={() => setTab(t)}>
            {t}{counts[t] !== undefined && <span className="count">{counts[t]}</span>}
          </button>
        ))}
      </nav>
      <div role="tabpanel">
        {tab === "Overview" && <Overview {...props} />}
        {tab === "Services" && <Services {...props} />}
        {tab === "Modules" && <Modules {...props} />}
        {tab === "Endpoints" && <Endpoints {...props} />}
        {tab === "Data model" && <DataModel {...props} />}
        {tab === "Stores & outside" && <Stores {...props} />}
        {tab === "Unclear" && <Unclear {...props} />}
      </div>
    </section>
  );
}

const Src = ({ link, file, line }: { link: Link; file: string; line?: number | null }) => (
  <a className="src" href={link(file, line)} target="_blank" rel="noreferrer">{file}{line ? `:${line}` : ""}</a>
);

/** Narrative text with [fact:<id>] citations turned into clickable chips (never HTML). */
function Cited({ text, view, onSelect }: { text: string; view: View; onSelect: (id: string) => void }) {
  const parts = text.split(/(\[fact:[^\]]+\])/g);
  return (
    <>{parts.map((part, i) => {
      const match = /^\[fact:([^\]]+)\]$/.exec(part);
      if (!match) return <span key={i}>{part}</span>;
      const id = match[1];
      return (
        <button key={i} className="cite" onClick={() => onSelect(id)} title={id}>
          {view.facts[id]?.name ?? "source"}
        </button>
      );
    })}</>
  );
}

function Overview({ view, onSelect }: Props) {
  return (
    <div className="overview">
      {view.summary && <><h3>How it is organized</h3><p>{view.summary.organization}</p></>}
      {view.sections.map((s, i) => {
        if (s.type === "narrative") {
          return <article key={i}><h3>{s.title}</h3><p><Cited text={s.body} view={view} onSelect={onSelect} /></p></article>;
        }
        if (s.type === "flow") {
          return (
            <article key={i} className="flow">
              <h3>{s.title}</h3>
              <p className="muted">{s.description}</p>
              <ol>{s.steps.map((step, j) => (
                <li key={j}><button className="cite" onClick={() => onSelect(factId(step))}>
                  {view.facts[factId(step)]?.name ?? factId(step)}</button></li>))}</ol>
            </article>
          );
        }
        return null;
      })}
    </div>
  );
}

function Modules({ name, view }: Props) {
  return (
    <div className="cards">
      {view.panels.modules.map((m) => (
        <article key={m.id} className="card">
          <h3><a href={routes.module(name, m.id)}>{m.name}</a></h3>
          <p className="muted">{m.classes.length} classes · {m.build_tools.join(", ")}
            {m.service && " · runs as an app"}</p>
          {m.summary && <p>{m.summary}</p>}
        </article>
      ))}
    </div>
  );
}

function Services({ name, view, link }: Props) {
  const modules = new Map(view.panels.modules.map((m) => [m.id, m]));
  return (
    <div className="cards">
      {view.panels.services.map((s) => {
        const module = s.module ? modules.get(s.module) : undefined;
        return (
          <article key={s.id} className="card">
            <h3>{s.module ? <a href={routes.module(name, s.module)}>{s.name}</a> : s.name}</h3>
            <p className="muted">port {s.port ?? "not set"}{s.roles.length > 0 && <> · {s.roles.join(", ")}</>}</p>
            {s.summary && <p>{s.summary}</p>}
            {module && module.capabilities.length > 0 && (
              <ul>{module.capabilities.map((c, i) => <li key={i}>{c}</li>)}</ul>)}
            <Src link={link} file={s.file} line={s.line} />
          </article>
        );
      })}
      {view.panels.services.length === 0 && <p className="muted">No Spring Boot application found.</p>}
    </div>
  );
}

function Endpoints({ name, view, link }: Props) {
  const service = new Map(view.panels.services.map((s) => [s.id, s.name]));
  return (
    <div className="table-wrap"><table className="table">
      <thead><tr><th>Method</th><th>Path</th><th>Branches</th><th>Inputs</th><th>Returns</th><th>Service</th><th>Handler</th></tr></thead>
      <tbody>{view.panels.endpoints.map((e) => (
        <tr key={e.id}>
          <td><span className={`method m-${e.method}`}>{e.method}</span></td>
          <td><a href={routes.endpoint(name, e.id)}><code>{e.path}</code></a>{!e.path_resolved && <span className="tag guess">path from a constant</span>}</td>
          <td><a href={routes.endpoint(name, e.id)}>{e.branches > 0 ? `${e.branches} →` : "map →"}</a></td>
          <td>{e.inputs.map((input, i) => (
            <span key={i} className="input" title={input.implicit ? "bound implicitly by Spring" : ""}>
              {input.name}<small> {input.source}{input.validated ? " ✓valid" : ""}</small></span>))}</td>
          <td><code>{e.returns}</code> <small className="muted">{e.response}</small></td>
          <td>{e.service ? service.get(e.service) : "-"}</td>
          <td><Src link={link} file={e.file} line={e.line} /><div className="muted small">{e.handler}</div></td>
        </tr>))}
      </tbody>
    </table></div>
  );
}

function DataModel({ view, link }: Props) {
  const table = new Map(view.panels.entities.map((e) => [e.id, e.table]));
  return (
    <div className="cards">
      {view.panels.entities.map((e) => (
        <article key={e.id} className="card">
          <h3>{e.table}{e.table_source === "default" && <span className="tag">default name</span>}</h3>
          <p className="muted">{e.class}</p>
          {e.relations.length > 0 && <ul>{e.relations.map((r, i) => (
            <li key={i}>{r.field}: <strong>{r.kind}</strong> → {table.get(r.target) ?? r.target}
              <span className={`conf-${r.confidence}`}> ({r.confidence})</span></li>))}</ul>}
          {e.repositories.length > 0 && <p className="muted">stored by {e.repositories.join(", ")}</p>}
          <Src link={link} file={e.file} line={e.line} />
        </article>
      ))}
      {view.panels.entities.length === 0 && <p className="muted">No JPA entities found.</p>}
    </div>
  );
}

function Stores({ view, link }: Props) {
  const name = (id: string) => view.panels.services.find((s) => s.id === id)?.name ?? id;
  const rows = [
    ...view.panels.data_stores.map((s) => ({ ...s, outside: false })),
    ...view.panels.external_systems.map((s) => ({ ...s, outside: true })),
  ];
  return (
    <div className="cards">
      {rows.map((s) => (
        <article key={s.id} className={`card${s.outside ? " dashed" : ""}`}>
          <h3>{s.name}{s.outside && <span className="tag">not in this repo</span>}</h3>
          <ul>{s.used_by.map((u, i) => (
            <li key={i}>{name(u.service)} <em>{u.kind}</em> <span className={`conf-${u.confidence}`}>({u.confidence})</span>
              {" "}<Src link={link} file={u.file} line={u.line} /></li>))}</ul>
          {s.locations.length > 0 && <p className="muted small">{s.locations.map((l) => l.value).join(" · ")}</p>}
        </article>
      ))}
      {rows.length === 0 && <p className="muted">No data stores or outside systems found.</p>}
    </div>
  );
}

function Unclear({ view, link }: Props) {
  return (
    <ul className="unclear">
      {view.panels.unclear.map((item, i) => (
        <li key={i}><span className={`tag k-${item.kind}`}>{item.kind.replace("_", " ")}</span> {item.text}
          {item.file && <> · <Src link={link} file={item.file} line={item.line} /></>}</li>))}
      {view.panels.unclear.length === 0 && <li className="muted">Nothing unclear.</li>}
    </ul>
  );
}
