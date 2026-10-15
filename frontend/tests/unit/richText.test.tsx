import { readFileSync } from 'node:fs';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { richDescription } from '../../src/app/richDescription';
import { parseJobDetail } from '../../src/lib/jobDetail';
import {
  parseFormatting,
  safeRichHref,
  type RichBlock,
} from '../../src/lib/richText';

// Structural blocks with placeholder words and reserved example.com links;
// no posting fixtures or API responses.
const LONG_URL = `https://example.com/${'segment/'.repeat(30)}`;
const BLOCKS: RichBlock[] = [
  {
    type: 'paragraph',
    content: [
      { type: 'text', text: 'Lead ', bold: false, italic: false },
      { type: 'text', text: 'strong', bold: true, italic: false },
      { type: 'text', text: ' and ', bold: false, italic: false },
      { type: 'text', text: 'slanted', bold: false, italic: true },
      { type: 'break' },
      {
        type: 'link',
        href: 'https://example.com/a',
        text: 'linked',
        bold: false,
        italic: false,
      },
      { type: 'text', text: ' ', bold: false, italic: false },
      {
        type: 'link',
        href: LONG_URL,
        text: LONG_URL,
        bold: false,
        italic: false,
      },
    ],
  },
  {
    type: 'list',
    ordered: false,
    items: [
      [
        {
          type: 'paragraph',
          content: [
            { type: 'text', text: 'first', bold: false, italic: false },
          ],
        },
        {
          type: 'list',
          ordered: true,
          items: [
            [
              {
                type: 'paragraph',
                content: [
                  { type: 'text', text: 'nested', bold: false, italic: false },
                ],
              },
            ],
          ],
        },
      ],
    ],
  },
];

describe('richDescription', () => {
  it('renders paragraphs, lists, emphasis and safe new-tab links as elements', () => {
    const { container } = render(<>{richDescription(BLOCKS, 'body-sm')}</>);
    const prose = container.querySelector('.rich-text .typography-prose');
    expect(prose).not.toBeNull();
    expect(screen.getByText('strong').tagName).toBe('STRONG');
    expect(screen.getByText('slanted').tagName).toBe('EM');
    expect(container.querySelectorAll('br')).toHaveLength(1);
    const link = screen.getByRole('link', { name: 'linked' });
    expect(link).toHaveAttribute('href', 'https://example.com/a');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByRole('link', { name: LONG_URL })).toBeInTheDocument();
    const outer = container.querySelector('ul')!;
    expect(outer.querySelectorAll(':scope > li')).toHaveLength(1);
    expect(outer.querySelector('ol > li')).toHaveTextContent('nested');
    // Paragraph size follows the surrounding plain text.
    expect(
      container.querySelectorAll('[data-type="body-sm"]').length,
    ).toBeGreaterThan(0);
    expect(container.querySelector('[data-type="body"]')).toBeNull();
  });

  it('never renders an unsafe target even if one slipped past parsing', () => {
    render(
      <>
        {richDescription(
          [
            {
              type: 'paragraph',
              content: [
                {
                  type: 'link',
                  href: 'javascript:alert(1)',
                  text: 'withheld target',
                  bold: false,
                  italic: false,
                },
              ],
            },
          ],
          'body',
        )}
      </>,
    );
    expect(screen.getByText('withheld target').closest('a')).toBeNull();
  });

  it('builds React elements only, wraps long URLs and keeps native layout', () => {
    for (const file of [
      'src/app/richDescription.tsx',
      'src/app/JobDetail.tsx',
    ]) {
      const source = readFileSync(file, 'utf8');
      expect(source).not.toContain('dangerouslySetInnerHTML');
      expect(source).not.toContain('innerHTML');
    }
    const styles = readFileSync('src/styles/index.css', 'utf8');
    expect(styles).toMatch(
      /\.rich-text\s*\{[^}]*white-space:\s*normal;[^}]*overflow-wrap:\s*anywhere/,
    );
    expect(styles).not.toMatch(/typography-prose|\.link\b/);
  });
});

describe('parseFormatting', () => {
  const labels = new Set(['Job Summary:']);
  const valid = { version: 'rich-v1', fields: { 'Job Summary:': BLOCKS } };

  it('accepts whitelisted blocks for saved labels', () => {
    expect(parseFormatting(valid, labels)).toEqual(valid);
    expect(parseFormatting(null, labels)).toBeNull();
    expect(parseFormatting(undefined, labels)).toBeNull();
  });

  it.each([
    ['other version', { ...valid, version: 'rich-v2' }],
    ['unknown label', { ...valid, fields: { 'Other:': BLOCKS } }],
    ['empty fields', { ...valid, fields: {} }],
    ['extra key', { ...valid, html: '<b>x</b>' }],
    [
      'unknown block',
      { ...valid, fields: { 'Job Summary:': [{ type: 'html', html: '<b>' }] } },
    ],
    [
      'unsafe link',
      {
        ...valid,
        fields: {
          'Job Summary:': [
            {
              type: 'paragraph',
              content: [
                { type: 'link', href: 'javascript:alert(1)', text: 'x' },
              ],
            },
          ],
        },
      },
    ],
    [
      'empty text',
      {
        ...valid,
        fields: {
          'Job Summary:': [
            { type: 'paragraph', content: [{ type: 'text', text: '' }] },
          ],
        },
      },
    ],
    [
      'too deep',
      {
        ...valid,
        fields: {
          'Job Summary:': Array.from({ length: 5 }).reduce<unknown[]>(
            (inner) => [{ type: 'list', ordered: false, items: [inner] }],
            [
              {
                type: 'paragraph',
                content: [{ type: 'text', text: 'x' }],
              },
            ],
          ),
        },
      },
    ],
  ])('drops all formatting for %s', (_name, value) => {
    expect(parseFormatting(value, labels)).toBeNull();
  });

  it('keeps plain mailto and http(s) targets only', () => {
    expect(safeRichHref('mailto:person@example.com')).toBe(
      'mailto:person@example.com',
    );
    expect(safeRichHref('mailto:person@example.com?subject=x')).toBeNull();
    expect(safeRichHref('https://example.com/')).toBe('https://example.com/');
    expect(safeRichHref('ftp://example.com/')).toBeNull();
  });

  it('falls back to plain text when a detail response carries bad formatting', () => {
    const detail = {
      posting_id: '1',
      listing: { id: '1', title: null },
      content: {
        id: '1',
        coverage: 'content_only',
        fields: { 'Job Summary:': 'Lead' },
      },
      content_observed_at: '2026-01-01T00:00:00+00:00',
      country_inference: { state: 'not_requested' },
    };
    expect(
      parseJobDetail({ ...detail, formatting: { version: 'x' } }, '1')
        .formatting,
    ).toBeNull();
    expect(
      parseJobDetail({ ...detail, formatting: valid }, '1').formatting,
    ).toEqual(valid);
    expect(parseJobDetail(detail, '1').formatting).toBeNull();
  });
});
