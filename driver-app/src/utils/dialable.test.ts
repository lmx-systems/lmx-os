import { dialable } from './dialable';

describe('dialable', () => {
  it('keeps the digits of a US number however it was typed', () => {
    expect(dialable('(512) 555-0142')).toBe('5125550142');
    expect(dialable('+1 512.555.0142')).toBe('+15125550142');
  });

  it('cuts an extension off rather than dialling its digits', () => {
    expect(dialable('512-555-0142 ext 31')).toBe('5125550142');
    expect(dialable('512-555-0142 x31')).toBe('5125550142');
  });

  it('drops a trunk prefix after a country code', () => {
    expect(dialable('+44 (0)20 7946 0018')).toBe('+442079460018');
  });

  it('refuses text that is not a whole number', () => {
    expect(dialable('call the front desk')).toBeNull();
    expect(dialable('555-0142')).toBe('5550142');
    expect(dialable('0142')).toBeNull();
    expect(dialable(null)).toBeNull();
  });
});
