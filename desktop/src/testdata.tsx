// How each kind of question shows its samples and test results (CLIENT_PROTOCOL.md section 4).
// The server does not (yet) say what kind a question is, so it is worked out from the
// language and the shape of the samples. If the server adds `kind` / `grader`, that wins.
import { Question, TestFeedback } from "./api";

export type Kind = "stdio" | "function" | "sql" | "ml" | "checks";

const MAX_ROWS = 300; // protocol: "first few hundred rows, with a row count"

function json(text: string | null | undefined): { ok: true; value: unknown } | { ok: false } {
  if (text == null) return { ok: false };
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false };
  }
}

const isTrainTest = (v: unknown): v is { train: string; test: string } =>
  !!v && typeof v === "object" && !Array.isArray(v) &&
  typeof (v as Record<string, unknown>).train === "string" && typeof (v as Record<string, unknown>).test === "string";

export function questionKind(q: Question): Kind {
  const declared = (q as { kind?: string; grader?: string }).kind ?? (q as { grader?: string }).grader;
  if (declared === "function" || declared === "sql" || declared === "ml" || declared === "stdio") return declared;
  if (declared === "html" || declared === "plot") return "checks";

  const keys = q.languages.map((l) => l.key);
  if (keys.includes("sql")) return "sql";
  if (keys.includes("html")) return "checks";
  const samples = q.samples;
  if (samples.length > 0) {
    if (
      samples.every((s) => {
        const v = json(s.input);
        return v.ok && isTrainTest(v.value);
      })
    )
      return "ml";
    // HTML / chart questions: each test is a list of checks and there is no expected output.
    if (samples.every((s) => !s.expected_output)) return "checks";
    // Function questions: arguments are a JSON list and the return value is JSON. Normal
    // (stdin/stdout) answers come from print(), so their expected output ends with a newline.
    if (
      samples.every((s) => {
        const a = json(s.input);
        return a.ok && Array.isArray(a.value) && json(s.expected_output).ok && !s.expected_output.endsWith("\n");
      })
    )
      return "function";
  }
  return "stdio";
}

// ---------------------------------------------------------------- tables

type Cell = string | number | boolean | null;

export function DataTable({
  columns,
  rows,
  caption,
  highlight,
}: {
  columns: string[] | null;
  rows: Cell[][];
  caption?: string;
  /** Row indexes to mark (e.g. wrong predictions). */
  highlight?: Set<number>;
}) {
  const width = Math.max(columns?.length ?? 0, ...rows.slice(0, MAX_ROWS).map((r) => r.length));
  const shown = rows.slice(0, MAX_ROWS);
  return (
    <div className="datatable">
      <div className="muted datatable-head">
        {caption && <strong>{caption}</strong>}
        <span>
          {rows.length} row{rows.length === 1 ? "" : "s"} × {width} column{width === 1 ? "" : "s"}
          {rows.length > MAX_ROWS && ` · showing the first ${MAX_ROWS}`}
        </span>
      </div>
      <div className="datatable-scroll">
        <table>
          {columns && (
            <thead>
              <tr>
                <th className="rownum">#</th>
                {columns.map((c, i) => (
                  <th key={i}>{c}</th>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {shown.map((r, i) => (
              <tr key={i} className={highlight?.has(i) ? "bad" : undefined}>
                <td className="rownum">{i + 1}</td>
                {Array.from({ length: width }, (_, j) => (
                  <td key={j} className={typeof r[j] === "number" ? "num" : undefined}>
                    {r[j] === null ? <span className="muted">NULL</span> : String(r[j] ?? "")}
                  </td>
                ))}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td className="muted" colSpan={width + 1}>
                  (no rows)
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** CSV text -> header + rows (quoted fields and "" escapes supported). */
export function parseCsv(text: string): { columns: string[]; rows: string[][] } {
  const out: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else quoted = false;
      } else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      out.push(row);
      row = [];
      field = "";
    } else field += ch;
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    out.push(row);
  }
  const lines = out.filter((r) => !(r.length === 1 && r[0] === ""));
  return { columns: lines[0] ?? [], rows: lines.slice(1) };
}

/** `{"columns": [...], "rows": [[...]]}` (SQL answers), or null if it isn't that. */
function sqlResult(text: string | null | undefined): { columns: string[]; rows: Cell[][] } | null {
  const v = json(text);
  if (!v.ok || !v.value || typeof v.value !== "object") return null;
  const { columns, rows } = v.value as { columns?: unknown; rows?: unknown };
  if (!Array.isArray(columns) || !Array.isArray(rows) || !rows.every(Array.isArray)) return null;
  return { columns: columns.map(String), rows: rows as Cell[][] };
}

/** INSERT statements -> one table per table name. Column names come from the INSERT
 *  itself or from `table(col, col)` in the question text. Null if anything looks unusual,
 *  and the SQL is shown as text instead. */
export function parseInserts(sql: string, statement: string): { table: string; columns: string[] | null; rows: Cell[][] }[] | null {
  const tables = new Map<string, { columns: string[] | null; rows: Cell[][] }>();
  let i = 0;
  const s = sql;
  const ws = () => {
    while (i < s.length) {
      if (/\s/.test(s[i])) i++;
      else if (s.startsWith("--", i)) {
        while (i < s.length && s[i] !== "\n") i++;
      } else break;
    }
  };
  const word = () => {
    ws();
    const m = /^[A-Za-z_][A-Za-z0-9_]*|^"[^"]+"|^`[^`]+`/.exec(s.slice(i));
    if (!m) return null;
    i += m[0].length;
    return m[0].replace(/^["`]|["`]$/g, "");
  };
  const expect = (ch: string) => {
    ws();
    if (s[i] !== ch) return false;
    i++;
    return true;
  };
  const value = (): Cell | undefined => {
    ws();
    if (s[i] === "'") {
      let out = "";
      i++;
      while (i < s.length) {
        if (s[i] === "'") {
          if (s[i + 1] === "'") {
            out += "'";
            i += 2;
            continue;
          }
          i++;
          return out;
        }
        out += s[i++];
      }
      return undefined;
    }
    const m = /^-?\d+(\.\d+)?([eE][-+]?\d+)?|^NULL\b|^TRUE\b|^FALSE\b/i.exec(s.slice(i));
    if (!m) return undefined;
    i += m[0].length;
    const t = m[0].toUpperCase();
    if (t === "NULL") return null;
    if (t === "TRUE") return true;
    if (t === "FALSE") return false;
    return Number(m[0]);
  };

  while (true) {
    ws();
    if (i >= s.length) break;
    if (word()?.toUpperCase() !== "INSERT") return null;
    if (word()?.toUpperCase() !== "INTO") return null;
    const table = word();
    if (!table) return null;
    let columns: string[] | null = null;
    ws();
    if (s[i] === "(") {
      i++;
      columns = [];
      while (true) {
        const c = word();
        if (!c) return null;
        columns.push(c);
        if (expect(")")) break;
        if (!expect(",")) return null;
      }
    }
    if (word()?.toUpperCase() !== "VALUES") return null;
    const entry = tables.get(table) ?? { columns, rows: [] };
    if (!entry.columns && columns) entry.columns = columns;
    while (true) {
      if (!expect("(")) return null;
      const row: Cell[] = [];
      while (true) {
        const v = value();
        if (v === undefined) return null;
        row.push(v);
        if (expect(")")) break;
        if (!expect(",")) return null;
      }
      entry.rows.push(row);
      if (!expect(",")) break;
    }
    tables.set(table, entry);
    expect(";");
  }
  if (tables.size === 0) return null;
  return [...tables].map(([table, t]) => {
    let columns = t.columns;
    if (!columns) {
      // e.g. "Tables: `employees(id, name, salary, dept_id)`" in the statement
      const m = new RegExp(`\\b${table.replace(/[^A-Za-z0-9_]/g, "")}\\s*\\(([A-Za-z0-9_,\\s]+)\\)`, "i").exec(statement);
      const named = m?.[1].split(",").map((x) => x.trim()).filter(Boolean);
      if (named && named.length === Math.max(...t.rows.map((r) => r.length))) columns = named;
    }
    return { table, columns, rows: t.rows };
  });
}

// ---------------------------------------------------------------- samples

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="labeled">
      <div className="muted">{label}</div>
      {children}
    </div>
  );
}

/** JSON on one line with Python-like spacing: [2, 7, 11, 15], {"a": 1}. */
export function compactJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(compactJson).join(", ")}]`;
  if (v && typeof v === "object")
    return `{${Object.entries(v).map(([k, x]) => `${JSON.stringify(k)}: ${compactJson(x)}`).join(", ")}}`;
  return JSON.stringify(v) ?? String(v);
}

function Arguments({ text }: { text: string }) {
  const v = json(text);
  if (!v.ok || !Array.isArray(v.value)) return <pre>{text}</pre>;
  return (
    <ol className="args">
      {v.value.map((a, i) => (
        <li key={i}>
          <code>{compactJson(a)}</code>
        </li>
      ))}
    </ol>
  );
}

function JsonValue({ text }: { text: string | null | undefined }) {
  const v = json(text);
  return <pre>{v.ok ? compactJson(v.value) : (text ?? "")}</pre>;
}

function SqlTables({ sql, statement }: { sql: string; statement: string }) {
  const tables = parseInserts(sql, statement);
  if (!tables) return <pre>{sql}</pre>;
  return (
    <>
      {tables.map((t) => (
        <DataTable key={t.table} caption={t.table} columns={t.columns} rows={t.rows} />
      ))}
    </>
  );
}

function SqlResult({ text, caption }: { text: string | null | undefined; caption?: string }) {
  const r = sqlResult(text);
  if (!r) return <pre>{text ?? ""}</pre>;
  return <DataTable caption={caption} columns={r.columns} rows={r.rows} />;
}

function Checks({ text }: { text: string }) {
  const lines = text.split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
  return (
    <ul className="checks">
      {lines.map((l, i) => (
        <li key={i}>
          <code>{l}</code>
        </li>
      ))}
    </ul>
  );
}

/** ML data: training table, and the test table with the expected labels as a last column. */
function MlData({ input, expected }: { input: string; expected: string | null | undefined }) {
  const v = json(input);
  if (!v.ok || !isTrainTest(v.value)) return <pre>{input}</pre>;
  const train = parseCsv(v.value.train);
  const test = parseCsv(v.value.test);
  const labels = json(expected);
  const target = train.columns.find((c) => !test.columns.includes(c)) ?? "label";
  const withLabels =
    labels.ok && Array.isArray(labels.value) && labels.value.length === test.rows.length
      ? {
          columns: [...test.columns, `${target} (expected)`],
          rows: test.rows.map((r, i) => [...r, (labels.value as Cell[])[i]]),
        }
      : null;
  return (
    <>
      <DataTable caption="Training data (train_csv)" columns={train.columns} rows={train.rows} />
      {withLabels ? (
        <DataTable caption="Test data (test_csv) with the expected answers" columns={withLabels.columns} rows={withLabels.rows} />
      ) : (
        <>
          <DataTable caption="Test data (test_csv)" columns={test.columns} rows={test.rows} />
          {expected && (
            <Labeled label="Expected predictions">
              <JsonValue text={expected} />
            </Labeled>
          )}
        </>
      )}
    </>
  );
}

export function SampleView({ kind, input, expected, statement }: { kind: Kind; input: string; expected: string; statement: string }) {
  switch (kind) {
    case "function":
      return (
        <div className="sample">
          <Labeled label="Arguments">
            <Arguments text={input} />
          </Labeled>
          <Labeled label="Returns">
            <JsonValue text={expected} />
          </Labeled>
        </div>
      );
    case "sql":
      return (
        <div className="sample stacked">
          <Labeled label="Data in the tables">
            <SqlTables sql={input} statement={statement} />
          </Labeled>
          <Labeled label="Expected result">
            <SqlResult text={expected} />
          </Labeled>
        </div>
      );
    case "ml":
      return (
        <div className="sample stacked">
          <MlData input={input} expected={expected} />
        </div>
      );
    case "checks":
      return (
        <div className="sample stacked">
          <Labeled label="Your answer must pass these checks">
            <Checks text={input} />
          </Labeled>
        </div>
      );
    default:
      return (
        <div className="sample">
          <Labeled label="Input">
            <pre>{input}</pre>
          </Labeled>
          <Labeled label="Expected output">
            <pre>{expected}</pre>
          </Labeled>
        </div>
      );
  }
}

// ---------------------------------------------------------------- results

const ML_PREFIX = "AEGIS_RESULT:";

function MlPredictions({ stdout, expected }: { stdout: string; expected: string | null | undefined }) {
  const mine = json(stdout.startsWith(ML_PREFIX) ? stdout.slice(ML_PREFIX.length) : stdout);
  if (!mine.ok || !Array.isArray(mine.value)) return stdout ? <pre>{stdout}</pre> : null;
  const exp = json(expected);
  const expList = exp.ok && Array.isArray(exp.value) ? (exp.value as Cell[]) : null;
  const preds = mine.value as Cell[];
  const wrong = new Set<number>();
  const rows = preds.map((p, i) => {
    const e = expList?.[i];
    if (expList && String(e) !== String(p)) wrong.add(i);
    return expList ? [p, e ?? null] : [p];
  });
  return (
    <details>
      <summary className="muted">
        Your predictions ({preds.length}
        {expList && `, ${wrong.size} different from expected`})
      </summary>
      <DataTable columns={expList ? ["yours", "expected"] : ["yours"]} rows={rows} highlight={wrong} />
    </details>
  );
}

function CheckLines({ stdout }: { stdout: string }) {
  const lines = stdout.split("\n").filter((l) => l.trim());
  return (
    <ul className="checks result">
      {lines.map((l, i) => {
        const pass = l.startsWith("✓");
        const fail = l.startsWith("✗");
        return (
          <li key={i} className={pass ? "ok" : fail ? "bad" : undefined}>
            <span className="mark">{pass ? "✓" : fail ? "✗" : "•"}</span>
            <code>{pass || fail ? l.slice(1).trim() : l}</code>
          </li>
        );
      })}
    </ul>
  );
}

export function TestResultView({ kind, test: t, statement }: { kind: Kind; test: TestFeedback; statement: string }) {
  const errors = t.stderr ? (
    <Labeled label={kind === "function" || kind === "ml" ? "Printed by your code / errors" : "Errors"}>
      <pre className={t.verdict === "RE" || t.verdict === "CE" ? "out bad" : "out"}>{t.stderr}</pre>
    </Labeled>
  ) : null;

  switch (kind) {
    case "function":
      return (
        <>
          <div className="sample">
            {t.input != null && (
              <Labeled label="Arguments">
                <Arguments text={t.input} />
              </Labeled>
            )}
            {t.expected_output != null && (
              <Labeled label="Expected return">
                <JsonValue text={t.expected_output} />
              </Labeled>
            )}
            <Labeled label="Your function returned">
              <JsonValue text={t.stdout} />
            </Labeled>
          </div>
          {errors}
        </>
      );
    case "sql":
      return (
        <>
          {t.input != null && (
            <details>
              <summary className="muted">Data in the tables</summary>
              <SqlTables sql={t.input} statement={statement} />
            </details>
          )}
          <div className="sample">
            {t.expected_output != null && (
              <Labeled label="Expected result">
                <SqlResult text={t.expected_output} />
              </Labeled>
            )}
            <Labeled label="Your result">
              <SqlResult text={t.stdout?.trim()} />
            </Labeled>
          </div>
          {errors}
        </>
      );
    case "ml":
      return (
        <>
          {t.stdout && <MlPredictions stdout={t.stdout.trim()} expected={t.expected_output} />}
          {errors}
        </>
      );
    case "checks":
      return (
        <>
          {t.stdout && <CheckLines stdout={t.stdout} />}
          {errors}
        </>
      );
    default:
      return (
        <>
          {t.input != null && (
            <div className="sample">
              <Labeled label="Input">
                <pre>{t.input}</pre>
              </Labeled>
              <Labeled label="Expected">
                <pre>{t.expected_output}</pre>
              </Labeled>
              <Labeled label="Your output">
                <pre>{t.stdout}</pre>
              </Labeled>
            </div>
          )}
          {errors}
        </>
      );
  }
}
