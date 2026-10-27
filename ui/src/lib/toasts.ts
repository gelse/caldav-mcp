/**
 * Toast + banner stores (UI-M2 design (b) and (d)).
 *
 * - Toasts: transient, auto-dismissed (success/info) or manual (errors);
 *   rendered inside an ARIA live region.
 * - Banners: persistent warnings (reload failure, topology restart, …)
 *   until explicitly dismissed.
 *
 * reportMutation() maps a successful mutation body onto these: signal
 * fields → warning banners, no signal fields → an "applied live" toast.
 */

import { writable } from 'svelte/store';
import { mutationSignals } from './api';

export type ToastKind = 'success' | 'info' | 'error';

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

export interface Banner {
  id: number;
  tone: 'warning' | 'danger';
  message: string;
}

export const toasts = writable<Toast[]>([]);
export const banners = writable<Banner[]>([]);

let nextId = 1;
const TOAST_TTL_MS = 6000;

export function toast(message: string, kind: ToastKind = 'info'): number {
  const id = nextId++;
  toasts.update((items) => [...items, { id, kind, message }]);
  if (kind !== 'error') {
    setTimeout(() => dismissToast(id), TOAST_TTL_MS);
  }
  return id;
}

export function banner(message: string, tone: 'warning' | 'danger' = 'warning'): number {
  const id = nextId++;
  banners.update((items) => [...items, { id, tone, message }]);
  return id;
}

export function dismissToast(id: number): void {
  toasts.update((items) => items.filter((item) => item.id !== id));
}

export function dismissBanner(id: number): void {
  banners.update((items) => items.filter((item) => item.id !== id));
}

/** Reset both regions — logout and mid-session 401 wipe the shell. */
export function clearSignals(): void {
  toasts.set([]);
  banners.set([]);
}

/**
 * Route a successful mutation body to banners/toasts per design (b):
 * `reload: "failed"` and `config_topology_restart_required` (plus the
 * defensive `restart_required`) become persistent warning banners; when
 * neither signal field is present the change is reported as "applied
 * live". Pass `appliedLiveMessage: null` when the calling view shows its
 * own success state (rotate-key dialog) and only banners should surface.
 */
export function reportMutation(body: unknown, appliedLiveMessage: string | null): void {
  const signals = mutationSignals(body);
  if (signals.reloadFailed) {
    banner('Saved, but live-apply failed — a server restart applies it.', 'warning');
  }
  if (signals.restartRequired) {
    banner(
      'The granted config is not in the server’s started topology — a server restart is required.',
      'warning',
    );
  }
  if (!signals.reloadFailed && !signals.restartRequired && appliedLiveMessage !== null) {
    toast(appliedLiveMessage, 'success');
  }
}
