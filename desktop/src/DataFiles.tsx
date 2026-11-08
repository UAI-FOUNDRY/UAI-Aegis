// Data files that come with a question (e.g. marks.csv). The files stay on the server,
// where the student's code runs and opens them by name; the app only shows a summary
// (first rows, column types and ranges), fetched when a file is opened here.
import { useState } from "react";
import { api, ColumnSummary, DataFile, DataFileDetail, errorMessage } from "./api";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatNumber(n: number | undefined): string {
  if (n === undefined || n === null) return "";
  if (Number.isInteger(n)) return n.toLocaleString();
  return Math.abs(n) >= 1000 ? n.toFixed(1) : String(Number(n.toPrecision(5)));
}

function shape(f: DataFile): string {
  const parts = [formatSize(f.size_bytes)];
  if (f.kind === "table" && f.rows !== null) {
    parts.push(`${f.rows.toLocaleString()} rows × ${f.column_count ?? 0} columns`);
  } else if (f.kind === "binary") {
    parts.push("binary");
  }
  return parts.join(" · ");
}

function ColumnsTable({ columns, statsRows, rows }: { columns: ColumnSummary[]; statsRows?: number; rows?: number }) {
  return (
    <div className="datatable">
      <div className="muted datatable-head">
        <span>
          {columns.length} column{columns.length === 1 ? "" : "s"}
          {statsRows !== undefined && rows !== undefined && statsRows < rows
            ? ` · figures from the first ${statsRows.toLocaleString()} rows`
            : ""}
        </span>
      </div>
      <div className="datatable-scroll">
        <table>
          <thead>
            <tr>
              <th>Column</th>
              <th>Type</th>
              <th>Filled</th>
              <th>Missing</th>
              <th>Distinct</th>
              <th>Min</th>
              <th>Max</th>
              <th>Mean / examples</th>
            </tr>
          </thead>
          <tbody>
            {columns.map((c) => (
              <tr key={c.name}>
                <td>
                  <strong>{c.name}</strong>
                </td>
                <td>{c.type}</td>
                <td className="num">{c.filled.toLocaleString()}</td>
                <td className={`num ${c.missing > 0 ? "warn" : ""}`}>{c.missing.toLocaleString()}</td>
                <td className="num">{c.distinct === null ? "1000+" : c.distinct.toLocaleString()}</td>
                <td className="num">{c.type === "number" ? formatNumber(c.min) : ""}</td>
                <td className="num">{c.type === "number" ? formatNumber(c.max) : ""}</td>
                <td className={c.type === "number" ? "num" : undefined}>
                  {c.type === "number" ? formatNumber(c.mean) : (c.examples ?? []).join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RowsTable({ header, rows, total }: { header: string[]; rows: string[][]; total: number }) {
  const width = Math.max(header.length, ...rows.map((r) => r.length));
  return (
    <div className="datatable">
      <div className="muted datatable-head">
        <span>
          {rows.length < total
            ? `First ${rows.length} of ${total.toLocaleString()} rows`
            : `${total.toLocaleString()} row${total === 1 ? "" : "s"}`}
        </span>
      </div>
      <div className="datatable-scroll">
        <table>
          <thead>
            <tr>
              <th className="rownum">#</th>
              {Array.from({ length: width }, (_, j) => (
                <th key={j}>{header[j] ?? ""}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="rownum">{i + 1}</td>
                {Array.from({ length: width }, (_, j) => (
                  <td key={j}>{r[j] ?? ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FileDetail({ detail }: { detail: DataFileDetail }) {
  const [tab, setTab] = useState<"rows" | "columns">("rows");
  const meta = detail.meta ?? {};
  if (detail.kind === "table") {
    const rows = Array.isArray(meta.preview) ? meta.preview : [];
    return (
      <div className="filedetail">
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "rows"} className={tab === "rows" ? "on" : ""} onClick={() => setTab("rows")}>
            First rows
          </button>
          <button
            role="tab"
            aria-selected={tab === "columns"}
            className={tab === "columns" ? "on" : ""}
            onClick={() => setTab("columns")}
          >
            Columns
          </button>
        </div>
        {tab === "rows" ? (
          <RowsTable header={meta.header ?? []} rows={rows} total={meta.rows ?? rows.length} />
        ) : (
          <ColumnsTable columns={meta.columns ?? []} statsRows={meta.stats_rows} rows={meta.rows} />
        )}
      </div>
    );
  }
  if (detail.kind === "text") {
    const text = typeof meta.preview === "string" ? meta.preview : "";
    return (
      <div className="filedetail">
        <p className="muted">
          {(meta.lines ?? 0).toLocaleString()} lines{text.length < detail.size_bytes ? " · showing the beginning" : ""}
        </p>
        <pre className="filetext">{text}</pre>
      </div>
    );
  }
  return (
    <div className="filedetail">
      <p className="muted">This file can't be shown here. Open it from your code.</p>
    </div>
  );
}

export default function DataFiles({
  sessionId,
  questionId,
  files,
}: {
  sessionId: string;
  questionId: string;
  files: DataFile[];
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, DataFileDetail>>({});
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = async (f: DataFile) => {
    setError(null);
    if (open === f.id) {
      setOpen(null);
      return;
    }
    setOpen(f.id);
    if (details[f.id]) return;
    setLoading(f.id);
    try {
      const d = await api.filePreview(sessionId, questionId, f.id);
      setDetails((all) => ({ ...all, [f.id]: d }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(null);
    }
  };

  return (
    <section className="datafiles">
      <h3>Data files</h3>
      <p className="muted">
        Your code opens these by name; they are next to your program when it runs. Click a file to look inside.
      </p>
      <ul>
        {files.map((f) => (
          <li key={f.id}>
            <button className={`fileitem ${open === f.id ? "on" : ""}`} onClick={() => toggle(f)} aria-expanded={open === f.id}>
              <span className="filename">{f.name}</span>
              <span className="muted">{shape(f)}</span>
            </button>
            <code className="readhint" title="Type this in your code">
              {f.read_hint}
            </code>
            {open === f.id &&
              (loading === f.id ? (
                <p className="muted">Loading…</p>
              ) : error ? (
                <p className="error">{error}</p>
              ) : details[f.id] ? (
                <FileDetail detail={details[f.id]} />
              ) : null)}
          </li>
        ))}
      </ul>
    </section>
  );
}
