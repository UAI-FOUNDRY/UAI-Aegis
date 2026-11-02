// Monaco editor, bundled locally (no CDN: the CSP allows only the app's own files).
// Paste guard (CLIENT_PROTOCOL.md section 7): copy/paste inside the editor works,
// text copied from anywhere else is refused and reported as `paste_blocked`.
import { useEffect, useRef } from "react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import TsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";
import HtmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import CssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import JsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";

// Each language service needs its own worker (HTML questions crash the generic one).
self.MonacoEnvironment = {
  getWorker(_id: string, label: string) {
    if (label === "typescript" || label === "javascript") return new TsWorker();
    if (label === "html" || label === "handlebars" || label === "razor") return new HtmlWorker();
    if (label === "css" || label === "scss" || label === "less") return new CssWorker();
    if (label === "json") return new JsonWorker();
    return new EditorWorker();
  },
};

// Text copied inside any editor during this run of the app. Shared by all questions,
// so copying from Q1 into Q2 is allowed.
const internalClipboard = new Set<string>();
const normalise = (t: string) => t.replace(/\r\n/g, "\n");

type Props = {
  value: string;
  monacoLanguage: string;
  readOnly: boolean;
  onChange: (code: string) => void;
  onBlockedPaste: (chars: number) => void;
};

export default function CodeEditor({ value, monacoLanguage, readOnly, onChange, onBlockedPaste }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const handlers = useRef({ onChange, onBlockedPaste });
  handlers.current = { onChange, onBlockedPaste };

  useEffect(() => {
    const el = host.current!;
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const ed = monaco.editor.create(el, {
      value,
      language: monacoLanguage,
      theme: dark ? "vs-dark" : "vs",
      readOnly,
      automaticLayout: true,
      minimap: { enabled: false },
      fontSize: 14,
      tabSize: 4,
      scrollBeyondLastLine: false,
      contextmenu: false, // its Paste item would bypass the guard below
      dragAndDrop: false,
      dropIntoEditor: { enabled: false },
      pasteAs: { enabled: false },
      links: false,
    });
    editor.current = ed;
    const sub = ed.onDidChangeModelContent(() => handlers.current.onChange(ed.getValue()));

    // Remember what is copied/cut inside the editor (bubble phase: after Monaco filled the clipboard).
    const remember = (e: ClipboardEvent) => {
      const text = e.clipboardData?.getData("text/plain");
      if (text) internalClipboard.add(normalise(text));
      const sel = ed.getSelection();
      const model = ed.getModel();
      if (sel && model && !sel.isEmpty()) internalClipboard.add(normalise(model.getValueInRange(sel)));
    };
    // Check pastes before Monaco sees them (capture phase).
    const guard = (e: ClipboardEvent) => {
      const text = normalise(e.clipboardData?.getData("text/plain") ?? "");
      if (text && !internalClipboard.has(text)) {
        e.preventDefault();
        e.stopPropagation();
        handlers.current.onBlockedPaste(text.length);
      }
    };
    const blockDrop = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
    };
    el.addEventListener("copy", remember);
    el.addEventListener("cut", remember);
    el.addEventListener("paste", guard, true);
    el.addEventListener("drop", blockDrop, true);
    return () => {
      el.removeEventListener("copy", remember);
      el.removeEventListener("cut", remember);
      el.removeEventListener("paste", guard, true);
      el.removeEventListener("drop", blockDrop, true);
      sub.dispose();
      ed.getModel()?.dispose();
      ed.dispose();
      editor.current = null;
    };
    // Created once per question (the parent gives each question its own key).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Outside changes (switching language loads starter code): replace without losing undo history.
  useEffect(() => {
    const ed = editor.current;
    if (ed && ed.getValue() !== value) {
      ed.executeEdits("app", [{ range: ed.getModel()!.getFullModelRange(), text: value }]);
    }
  }, [value]);

  useEffect(() => {
    const model = editor.current?.getModel();
    if (model) monaco.editor.setModelLanguage(model, monacoLanguage);
  }, [monacoLanguage]);

  useEffect(() => {
    editor.current?.updateOptions({ readOnly });
  }, [readOnly]);

  return <div className="editor" ref={host} />;
}
