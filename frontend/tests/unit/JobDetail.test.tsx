import { readFileSync } from 'node:fs';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from '../../src/app/App';
import { JobDetailContent } from '../../src/app/JobDetail';

afterEach(() => vi.unstubAllGlobals());

it('removes only the standalone keywords/skills presentation without fabricated postings', () => {
  const source = readFileSync('src/app/JobDetail.tsx', 'utf8');
  expect(source).not.toContain('Keywords and skills');
  expect(source).not.toContain('Extracted keywords');
  expect(source).not.toContain('Validated skill mentions');
  expect(source).not.toContain('uniqueSkillMentions');
  expect(source).toContain('analysis.keyword_extraction.state');
  expect(source).toContain('Similar jobs');
  expect(source).toContain('Weighted keyword coverage—not eligibility.');
  expect(source).toContain('contentCards(content.fields)');
});
it('keeps Back available for invalid direct URLs without transport', async () => {
  const transport = vi.fn();
  vi.stubGlobal('fetch', transport);
  window.history.replaceState(null, '', '/?job=invalid&q=preserve&page=2');
  render(<App state={{ status: 'disconnected' }} />);
  const back = screen.getByRole('link', { name: 'Postings' });
  expect(back).toHaveAttribute('href', '/?q=preserve&page=2');
  expect(screen.getByLabelText('Posting navigation')).toBeInTheDocument();
  const title = screen.getByRole('heading', { name: 'Invalid posting ID' });
  expect(
    back.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  await waitFor(() =>
    expect(screen.getByText('Job details unavailable')).toBeInTheDocument(),
  );
  expect(
    screen.queryByRole('complementary', { name: 'Filters' }),
  ).not.toBeInTheDocument();
  expect(transport).not.toHaveBeenCalled();
  fireEvent.click(back);
  await waitFor(() =>
    expect(window.location.search).toBe('?q=preserve&page=2'),
  );
});
it('presents explicit loading and not-found states without inventing postings', () => {
  const { rerender } = render(
    <JobDetailContent state={{ id: '0', status: 'loading' }} />,
  );
  const loading = screen.getByRole('status', {
    name: 'Loading stored job details',
  });
  expect(loading).toHaveClass('posting-layout');
  expect(loading).toHaveAttribute('aria-busy', 'true');
  for (const column of ['facts', 'description', 'similar']) {
    const region = loading.querySelector(`.posting-${column}`)!;
    expect(region).toHaveAttribute('aria-hidden', 'true');
    expect(region.querySelector('.posting-column-heading')).not.toBeNull();
    expect(region.querySelector('.posting-column-content')).not.toBeNull();
    expect(region.querySelectorAll('.skeleton').length).toBeGreaterThan(5);
  }
  expect(loading.querySelectorAll('a, button, [tabindex="0"]')).toHaveLength(0);
  rerender(<JobDetailContent state={{ id: '0', status: 'not-found' }} />);
  expect(screen.getByText('Posting not found')).toBeInTheDocument();
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});
