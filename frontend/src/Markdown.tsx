import { Fragment, type ReactNode } from 'react';

/**
 * 极轻量的 Markdown 渲染：只处理对话里真正需要的语法。
 * 支持：段落 / 换行 / 有序、无序列表 / **加粗** / `行内代码` / # 标题。
 * 不引入任何第三方解析库；React 自带转义，天然防 XSS。
 */

function renderInline(text: string): ReactNode[] {
  const tokens = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter((token) => token.length > 0);
  const nodes: ReactNode[] = [];

  tokens.forEach((token, tokenIndex) => {
    const lines = token.split('\n');
    lines.forEach((line, lineIndex) => {
      if (lineIndex > 0) nodes.push(<br key={`br-${tokenIndex}-${lineIndex}`} />);
      if (!line) return;

      if (line.startsWith('**') && line.endsWith('**')) {
        nodes.push(<strong key={`b-${tokenIndex}-${lineIndex}`}>{line.slice(2, -2)}</strong>);
        return;
      }
      if (line.startsWith('`') && line.endsWith('`')) {
        nodes.push(<code key={`c-${tokenIndex}-${lineIndex}`}>{line.slice(1, -1)}</code>);
        return;
      }
      nodes.push(<Fragment key={`t-${tokenIndex}-${lineIndex}`}>{line}</Fragment>);
    });
  });

  return nodes;
}

const UNORDERED = /^\s*[-*•]\s+/;
const ORDERED = /^\s*\d+[.)]\s+/;
const HEADING = /^\s*#{1,4}\s+/;

function renderBlocks(text: string): ReactNode[] {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;
  let blockKey = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    if (HEADING.test(trimmed)) {
      const content = trimmed.replace(HEADING, '');
      blocks.push(
        <p className="md-heading" key={`h-${blockKey++}`}>
          {renderInline(content)}
        </p>
      );
      index += 1;
      continue;
    }

    if (UNORDERED.test(line)) {
      const items: string[] = [];
      while (index < lines.length && UNORDERED.test(lines[index])) {
        items.push(lines[index].replace(UNORDERED, ''));
        index += 1;
      }
      blocks.push(
        <ul key={`ul-${blockKey++}`}>
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInline(item)}</li>
          ))}
        </ul>
      );
      continue;
    }

    if (ORDERED.test(line)) {
      const items: string[] = [];
      while (index < lines.length && ORDERED.test(lines[index])) {
        items.push(lines[index].replace(ORDERED, ''));
        index += 1;
      }
      blocks.push(
        <ol key={`ol-${blockKey++}`}>
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInline(item)}</li>
          ))}
        </ol>
      );
      continue;
    }

    const paragraph: string[] = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !UNORDERED.test(lines[index]) &&
      !ORDERED.test(lines[index]) &&
      !HEADING.test(lines[index])
    ) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(
      <p key={`p-${blockKey++}`}>{renderInline(paragraph.join('\n'))}</p>
    );
  }

  return blocks;
}

export function Markdown({ text }: { text: string }) {
  return <div className="markdown">{renderBlocks(text)}</div>;
}
