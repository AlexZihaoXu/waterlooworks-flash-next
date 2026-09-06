import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RefreshConsole } from '../../src/app/RefreshActivity';

// Generic disclosure content, not log/API fixtures.
describe('compact refresh console', () => {
  it('previews three lines without Button styling, expands and collapses', () => {
    render(
      <RefreshConsole
        entries={['one', 'two', 'three', 'four'].map((key) => ({
          key,
          content: key,
        }))}
        status=""
        revision="1"
      />,
    );
    const trigger = screen.getByRole('button', { name: 'Expand refresh log' });
    expect(trigger).not.toHaveClass('button');
    expect(within(trigger).queryByText('one')).not.toBeInTheDocument();
    for (const line of ['two', 'three', 'four'])
      expect(within(trigger).getByText(line)).toBeInTheDocument();
    fireEvent.click(trigger);
    expect(screen.getByRole('log', { name: 'Refresh log' })).toBeVisible();
    expect(within(screen.getByRole('log')).getByText('one')).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Collapse refresh log' }),
    );
    expect(screen.queryByRole('log')).not.toBeInTheDocument();
  });
});
