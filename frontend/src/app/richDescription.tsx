import type { ReactNode } from 'react';
import { Typography } from '@heroui/react';
import type { RichBlock, RichInline } from '../lib/richText';
import { safeRichHref } from '../lib/richText';

type TextSize = 'body' | 'body-sm';

function renderInline(run: RichInline, key: number): ReactNode {
  if (run.type === 'break') return <br key={key} />;
  let node: ReactNode = run.text;
  if (run.italic) node = <em>{node}</em>;
  if (run.bold) node = <strong>{node}</strong>;
  const href = run.type === 'link' ? safeRichHref(run.href) : null;
  return href ? (
    <a key={key} href={href} target="_blank" rel="noopener noreferrer">
      {node}
    </a>
  ) : (
    <span key={key}>{node}</span>
  );
}

function renderBlocks(blocks: readonly RichBlock[], size: TextSize) {
  return blocks.map((block, index) => {
    if (block.type === 'paragraph')
      return (
        <Typography
          key={index}
          type={size}
          render={(props) => <div {...props} />}
        >
          {block.content.map(renderInline)}
        </Typography>
      );
    const items = block.items.map((item, itemIndex) => (
      <li key={itemIndex}>
        <div className="rich-text-flow">{renderBlocks(item, size)}</div>
      </li>
    ));
    return block.ordered ? (
      <ol key={index}>{items}</ol>
    ) : (
      <ul key={index}>{items}</ul>
    );
  });
}

/**
 * Saved description formatting as React elements (never HTML strings):
 * HeroUI Typography.Prose styles the native lists, links and emphasis, and
 * Typography sizes each paragraph to match the surrounding plain text.
 */
export function richDescription(
  blocks: readonly RichBlock[],
  size: TextSize,
): ReactNode {
  return (
    <div className="rich-text">
      <Typography.Prose>
        <div className="rich-text-flow">{renderBlocks(blocks, size)}</div>
      </Typography.Prose>
    </div>
  );
}
