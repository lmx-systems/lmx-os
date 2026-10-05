// `ApiError` lives in the API client, which reaches AsyncStorage through
// serverUrl.ts; the package's jest mock lets the client import.
jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);

import { ApiError } from '../api/client';
import { signInFailure } from './signInFailure';

describe('what a driver is told when sign-in fails', () => {
  it("passes on the server's own answer", () => {
    expect(signInFailure(new ApiError(429, 'Too many codes requested'), 'https://api.example.com')).toBe(
      'Too many codes requested',
    );
  });

  it('names the server it could not reach', () => {
    // The first Android build's failure: the built-in localhost is the phone.
    expect(signInFailure(new TypeError('Network request failed'), 'http://localhost:8000')).toBe(
      "Couldn't reach http://localhost:8000. Check the server address below.",
    );
  });
});
