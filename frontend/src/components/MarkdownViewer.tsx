// Minimal, dependency-free markdown renderer. Handles the subset the pipeline
// produces: headings, bold/italic, inline code, fenced code, lists, blockquotes,
// horizontal rules and paragraphs. Good enough for reading bibles/critic reports.

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function inline(s: string): string {
  let out = escapeHtml(s);
  out = out.replace(/`([^`]+)`/g, '<code class="rounded bg-ink-800 px-1 py-0.5 text-[0.85em] text-accent">$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a class="text-accent underline" href="$2" target="_blank" rel="noreferrer">$1</a>');
  return out;
}

function render(md: string): string {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const html: string[] = [];
  let inCode = false;
  let codeBuf: string[] = [];
  let listType: "ul" | "ol" | null = null;
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      html.push(`<p>${inline(para.join(" "))}</p>`);
      para = [];
    }
  };
  const flushList = () => {
    if (listType) {
      html.push(`</${listType}>`);
      listType = null;
    }
  };

  for (const raw of lines) {
    const line = raw;
    if (line.trim().startsWith("```")) {
      if (inCode) {
        html.push(`<pre class="my-3 overflow-x-auto rounded-lg bg-ink-950 p-3 text-xs text-gray-300"><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`);
        codeBuf = [];
        inCode = false;
      } else {
        flushPara();
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }

    if (!line.trim()) { flushPara(); flushList(); continue; }

    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushPara(); flushList();
      const level = h[1].length;
      const sizes = ["text-2xl", "text-xl", "text-lg", "text-base", "text-sm", "text-sm"];
      html.push(`<h${level} class="mt-4 mb-2 font-semibold ${sizes[level - 1]} text-gray-100">${inline(h[2])}</h${level}>`);
      continue;
    }
    if (/^(---|\*\*\*|___)\s*$/.test(line)) {
      flushPara(); flushList();
      html.push('<hr class="my-4 border-edge" />');
      continue;
    }
    if (/^>\s?/.test(line)) {
      flushPara(); flushList();
      html.push(`<blockquote class="my-2 border-l-2 border-accent/60 pl-3 text-gray-400 italic">${inline(line.replace(/^>\s?/, ""))}</blockquote>`);
      continue;
    }
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ol) {
      flushPara();
      if (listType !== "ol") { flushList(); listType = "ol"; html.push('<ol class="my-2 ml-5 list-decimal space-y-1">'); }
      html.push(`<li>${inline(ol[1])}</li>`);
      continue;
    }
    if (ul) {
      flushPara();
      if (listType !== "ul") { flushList(); listType = "ul"; html.push('<ul class="my-2 ml-5 list-disc space-y-1">'); }
      html.push(`<li>${inline(ul[1])}</li>`);
      continue;
    }
    para.push(line);
  }
  flushPara();
  flushList();
  if (inCode && codeBuf.length) {
    html.push(`<pre class="my-3 overflow-x-auto rounded-lg bg-ink-950 p-3 text-xs text-gray-300"><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`);
  }
  return html.join("\n");
}

export default function MarkdownViewer({ content, className = "" }: { content: string; className?: string }) {
  return (
    <div
      className={`markdown text-[0.95rem] leading-relaxed text-gray-300 ${className}`}
      dangerouslySetInnerHTML={{ __html: render(content || "") }}
    />
  );
}
