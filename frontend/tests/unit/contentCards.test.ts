import { expect, it } from 'vitest';
import { contentCards } from '../../src/lib/jobDetail';

it('groups metadata instead of creating one card per short field', () => {
  const cards = contentCards(
    Object.fromEntries(
      [
        'Job Type:',
        'Level:',
        'Number of Job Openings:',
        'Work Term:',
        'Work Term Duration:',
      ].map((key) => [key, '']),
    ),
  );
  expect(cards.map((card) => [card.title, card.fields.length])).toEqual([
    ['Position details', 3],
    ['Work term', 2],
  ]);
});
it('keeps narrative sections separate and preserves every known field once', () => {
  const fields = {
    'Job Summary:': '',
    'Required Skills:': '',
    'Job - City:': '',
    'Job - Country:': '',
  };
  const cards = contentCards(fields);
  expect(cards.map((card) => card.title)).toEqual([
    'Job Summary',
    'Required Skills',
    'Location',
  ]);
  expect(
    cards.flatMap((card) => card.fields.map(([key]) => key)).sort(),
  ).toEqual(Object.keys(fields).sort());
  expect(contentCards({})).toEqual([]);
});
