import { ApiError } from '../api/client';

/**
 * What to tell a driver whose sign-in didn't go through.
 *
 * The server's own answer, when there was one. A request that never reached a
 * server isn't "something went wrong": on a fresh install it is nearly always
 * the address, which is built in as localhost, and on a phone that means the
 * phone. Naming the server says where to look, and the sign-in screen has the
 * way to change it.
 */
export function signInFailure(err: unknown, server: string): string {
  if (err instanceof ApiError) return err.message;
  return `Couldn't reach ${server}. Check the server address below.`;
}
