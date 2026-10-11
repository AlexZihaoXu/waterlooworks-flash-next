import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RefreshConsole } from '../../src/app/RefreshActivity';

// Generic disclosure content, not log/API fixtures.
describe('compact refresh console', () => {
  it('stays fully collapsed without Button styling until opened', () => {
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
    expect(screen.queryByRole('log')).not.toBeInTheDocument();
    for (const line of ['one', 'two', 'three', 'four'])
      expect(within(trigger).queryByText(line)).not.toBeInTheDocument();
    fireEvent.click(trigger);
    expect(screen.getByRole('log', { name: 'Refresh log' })).toBeVisible();
    expect(within(screen.getByRole('log')).getByText('one')).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Collapse refresh log' }),
    );
    expect(screen.queryByRole('log')).not.toBeInTheDocument();
  });
});
