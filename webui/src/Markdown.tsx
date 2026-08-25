import { memo, useCallback, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

/**
 * Assistant output is markdown. It used to land in a text node, so every answer
 * showed its own `**` and backticks.
 *
 * Model output is untrusted: it can quote a hostile file, a scraped page, or a
 * tool result. `react-markdown` does not evaluate raw HTML unless
 * `rehype-raw` is added, and it is deliberately not added here, so any embedded
 * markup stays literal text rather than becoming an element.
 */

function CodeBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const copy = useCallback(() => {
    // navigator.clipboard is unavailable on insecure non-loopback origins.
    void navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      })
      .catch(() => setCopied(false));
  }, [text]);

  return (
    <div className="code-block">
      <button
        type="button"
        className="code-copy"
        onClick={copy}
        aria-label={copied ? "Copied to clipboard" : "Copy code to clipboard"}
      >
        {copied ? "Copied" : "Copy"}
      </button>
      <pre>
        <code>{text}</code>
      </pre>
    </div>
  );
}

const components: Components = {
  // Fenced blocks arrive as <pre><code>; unwrap so the copy button owns the frame.
  pre: ({ children }) => <>{children}</>,
  code: ({ className, children, ...rest }) => {
    const text = String(children ?? "").replace(/\n$/, "");
    const fenced = typeof className === "string" && className.startsWith("language-");
    if (fenced || text.includes("\n")) return <CodeBlock text={text} />;
    return (
      <code className="inline-code" {...rest}>
        {children}
      </code>
    );
  },
  // Links in model output point anywhere. Never leak the workspace URL, and
  // never let a target grab window.opener.
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow ugc">
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="md-table-scroll">
      <table>{children}</table>
    </div>
  ),
};

export const Markdown = memo(function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
});

export default Markdown;
