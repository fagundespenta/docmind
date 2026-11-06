import { Fragment, type ReactNode } from "react";

/** Render a model answer: paragraphs, "- " bullets, **bold**, and clickable [n] citations. */
export function Answer({
  text,
  onCite,
  activeCite,
}: {
  text: string;
  onCite: (n: number) => void;
  activeCite: number | null;
}) {
  const inline = (line: string, key: string): ReactNode[] =>
    line.split(/(\[\d+\]|\*\*[^*]+\*\*)/g).map((part, i) => {
      const cite = part.match(/^\[(\d+)\]$/);
      if (cite) {
        const n = Number(cite[1]);
        return (
          <button
            key={`${key}-${i}`}
            className={`cite ${activeCite === n ? "active" : ""}`}
            onClick={() => onCite(n)}
            title={`Show source ${n}`}
          >
            {n}
          </button>
        );
      }
      if (part.startsWith("**") && part.endsWith("**"))
        return <strong key={`${key}-${i}`}>{part.slice(2, -2)}</strong>;
      return <Fragment key={`${key}-${i}`}>{part}</Fragment>;
    });

  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (bullets.length) {
      const items = bullets;
      blocks.push(
        <ul key={`ul-${blocks.length}`}>
          {items.map((b, i) => (
            <li key={i}>{inline(b, `li-${blocks.length}-${i}`)}</li>
          ))}
        </ul>,
      );
      bullets = [];
    }
  };
  text.split("\n").forEach((line, i) => {
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    if (bullet) {
      bullets.push(bullet[1]);
      return;
    }
    flush();
    if (line.trim()) blocks.push(<p key={`p-${i}`}>{inline(line, `p-${i}`)}</p>);
  });
  flush();
  return <div className="answer">{blocks}</div>;
}
