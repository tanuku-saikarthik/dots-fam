import { api } from './api';

export interface CallsStatus {
  push_devices: number;
  ntfy: boolean;
  ntfy_topic: string | null;
  ntfy_server: string;
  public_url: string | null;
  public_key: string;
  recent: { at: number; title: string; body: string; push: number; ntfy: number }[];
}

export type PushSupport = 'ok' | 'insecure' | 'ios-install' | 'unsupported';

export function pushSupport(): PushSupport {
  if (!window.isSecureContext) return 'insecure';
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const installed = window.matchMedia('(display-mode: standalone)').matches;
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window))
    return ios && !installed ? 'ios-install' : 'unsupported';
  return 'ok';
}

export function registerServiceWorker() {
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* calls just stay off on this device */
    });
  });
}

function keyBytes(base64url: string) {
  const padded = (base64url + '='.repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

async function registration() {
  return (await navigator.serviceWorker.getRegistration()) ?? (await navigator.serviceWorker.register('/sw.js'));
}

export async function deviceSubscription(): Promise<PushSubscription | null> {
  if (pushSupport() !== 'ok') return null;
  const reg = await navigator.serviceWorker.getRegistration();
  return reg ? reg.pushManager.getSubscription() : null;
}

function deviceLabel() {
  const ua = navigator.userAgent;
  const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iPhone' : /Mac/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : 'Linux';
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : 'Safari';
  return `${browser} on ${os}`;
}

/** Ask for notification permission, subscribe this browser, and register it with the server. */
export async function enableCallsHere(publicKey: string): Promise<CallsStatus> {
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') throw new Error('Notifications are blocked for this site. Allow them in your browser settings, then try again.');
  const reg = await registration();
  await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub)
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) });
  const json = sub.toJSON();
  return api<CallsStatus>('/calls/devices', 'POST', { endpoint: json.endpoint, keys: json.keys, label: deviceLabel() });
}

export async function disableCallsHere(): Promise<CallsStatus | undefined> {
  const sub = await deviceSubscription();
  if (!sub) return undefined;
  const endpoint = sub.endpoint;
  await sub.unsubscribe();
  return api<CallsStatus>('/calls/devices/remove', 'POST', { endpoint, keys: {} });
}
