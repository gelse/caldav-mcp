/**
 * Gate status mapping (UI-M2 design (c)) — shared between App's silent
 * key validation on mount and GateView's form submission so both render
 * the same screens and wording.
 */

import { ApiError } from './api';

export type GateStatus =
  | 'idle'
  | 'checking'
  | 'invalid'
  | 'expired'
  | 'disabled'
  | 'unreachable'
  | 'ratelimited'
  | 'error';

export interface GateOutcome {
  status: GateStatus;
  message: string;
  retryAfter: number;
}

export const GATE_DISABLED_MESSAGE =
  'The admin API is not available on this server — it requires pro mode (DB_CONFIG_ENABLED) with CALDAV_MCP_ADMIN_KEY set. This is not a key problem.';

export const GATE_INVALID_MESSAGE =
  'Key invalid or expired — check the admin key and try again.';

export const GATE_EXPIRED_MESSAGE =
  'Key no longer valid — it was rotated or invalidated elsewhere, or the server restarted with a different key.';

/** Map a failed validation attempt onto the gate screens. */
export function gateOutcomeFor(error: unknown, expired = false): GateOutcome {
  if (error instanceof ApiError) {
    switch (error.kind) {
      case 'auth':
        return {
          status: expired ? 'expired' : 'invalid',
          message: expired ? GATE_EXPIRED_MESSAGE : GATE_INVALID_MESSAGE,
          retryAfter: 0,
        };
      case 'disabled':
        return { status: 'disabled', message: GATE_DISABLED_MESSAGE, retryAfter: 0 };
      case 'ratelimited':
        return {
          status: 'ratelimited',
          message: 'Too many failed attempts — wait for the countdown, then try again.',
          retryAfter: error.retryAfterSeconds ?? 5,
        };
      case 'network':
        return {
          status: 'unreachable',
          message: 'Server unreachable — could not contact the admin API.',
          retryAfter: 0,
        };
      default:
        // 500/503/… — surface the server's message (503 carries its retry hint).
        return { status: 'error', message: error.message, retryAfter: 0 };
    }
  }
  return { status: 'error', message: 'Something went wrong — try again.', retryAfter: 0 };
}
