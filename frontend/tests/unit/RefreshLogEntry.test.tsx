import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RefreshLogEntry } from '../../src/app/RefreshLogEntry';

describe('log entry composition', () => {
  it('retains readable severity labels and escapes text instead of rendering markup', () => {
    const { container } = render(
      <RefreshLogEntry
        time="00:00:00"
        level="warning"
        message="<strong>text</strong>"
      />,
    );
    expect(screen.getByText('[00:00:00]')).toHaveClass(
      'typography--color-muted',
    );
    expect(
      screen.getByText('WARNING').closest('[data-slot="chip"]'),
    ).toHaveClass('chip--warning');
    expect(screen.getByText('<strong>text</strong>')).toBeInTheDocument();
    expect(container.querySelector('strong')).toBeNull();
  });
});
