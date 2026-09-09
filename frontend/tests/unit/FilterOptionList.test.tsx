import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { FilterOptionList } from '../../src/app/FilterOptionList';
import { groupedOptionRows } from '../../src/lib/optionGroups';

// Generic option primitives in the existing HeroUI composition, no job data.
function Picker() {
  const [draft, setDraft] = useState(['alpha', 'unknown']);
  return (
    <>
      <FilterOptionList
        rows={groupedOptionRows(['alpha', 'alpine', 'beta'])}
        label="Primitive options"
        draft={draft}
        setDraft={setDraft}
      />
      <output aria-label="Draft">{draft.join(',')}</output>
    </>
  );
}
it('renders partial category state, selects all then clears only descendants with stable leaf DOM', () => {
  render(<Picker />);
  const heading = screen.getByRole('checkbox', {
    name: 'Select all in A (2 options)',
  });
  const leaf = screen.getByRole('checkbox', { name: 'alpha' });
  expect(heading).toBePartiallyChecked();
  fireEvent.click(heading);
  expect(heading).toBeChecked();
  expect(screen.getByLabelText('Draft')).toHaveTextContent(
    'alpha,unknown,alpine',
  );
  expect(screen.getByRole('checkbox', { name: 'alpha' })).toBe(leaf);
  fireEvent.click(heading);
  expect(heading).not.toBeChecked();
  expect(heading).not.toBePartiallyChecked();
  expect(screen.getByLabelText('Draft')).toHaveTextContent(/^unknown$/);
});
