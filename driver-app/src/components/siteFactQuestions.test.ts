import { siteFactQuestions } from './siteFactQuestions';

describe('siteFactQuestions', () => {
  it('asks nothing without facts, or when the server asks for none', () => {
    expect(siteFactQuestions(null)).toEqual([]);
    expect(
      siteFactQuestions({ kind_of_site: 'body_shop', setting: 'suburb', shares_address: true, ask: [] }),
    ).toEqual([]);
  });

  it('asks only what the server says is open', () => {
    const questions = siteFactQuestions({
      kind_of_site: 'body_shop',
      setting: null,
      shares_address: null,
      ask: ['setting', 'shares_address'],
    });
    expect(questions.map((q) => q.key)).toEqual(['shares_address', 'setting']);
  });

  it("marks the desk's guess so confirming it is one tap", () => {
    const [kind] = siteFactQuestions({
      kind_of_site: 'dealership_counter',
      setting: 'suburb',
      shares_address: true,
      ask: ['kind_of_site'],
    });
    const guessed = kind.options.filter((o) => o.guess);
    expect(guessed.map((o) => o.value)).toEqual(['dealership_counter']);
    expect(kind.prompt).toContain('guess');
  });

  it('offers not sure on every question, and yes or no for a shared address', () => {
    const questions = siteFactQuestions({
      kind_of_site: null,
      setting: null,
      shares_address: null,
      ask: ['kind_of_site', 'setting', 'shares_address'],
    });
    expect(questions.every((q) => q.notSure)).toBe(true);
    const shares = questions.find((q) => q.key === 'shares_address');
    expect(shares?.options.map((o) => o.value)).toEqual([true, false]);
    expect(shares?.options.some((o) => o.guess)).toBe(false);
  });

  it("uses the server's codes for kind and setting", () => {
    const questions = siteFactQuestions({
      kind_of_site: null,
      setting: null,
      shares_address: null,
      ask: ['kind_of_site', 'setting'],
    });
    expect(questions.find((q) => q.key === 'setting')?.options.map((o) => o.value)).toEqual(['city_core', 'suburb']);
    expect(questions.find((q) => q.key === 'kind_of_site')?.options).toHaveLength(7);
  });
});
