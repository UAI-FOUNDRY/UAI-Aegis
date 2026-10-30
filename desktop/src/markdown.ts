// Question statements are Markdown written by professors (CLIENT_PROTOCOL.md section 8):
// raw HTML is turned off in the parser AND the output is sanitised. Links and images
// are stripped: a click must never navigate the exam window away.
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";

const md = new MarkdownIt({ html: false, linkify: false, typographer: false });

export function renderMarkdown(source: string): string {
  return DOMPurify.sanitize(md.render(source ?? ""), {
    FORBID_TAGS: ["a", "img", "style", "form", "input", "button", "iframe"],
    FORBID_ATTR: ["href", "src", "style", "srcset", "action"],
    KEEP_CONTENT: true,
  });
}
