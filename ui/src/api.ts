import type { Config } from './types';

async function checkResponse(r: Response): Promise<void> {
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(body?.detail ?? `${r.status} ${r.statusText}`);
  }
}

export async function fetchConfig(): Promise<Config> {
  const r = await fetch('/api/config');
  await checkResponse(r);
  return r.json();
}

export async function saveConfig(cfg: Config): Promise<void> {
  const r = await fetch('/api/config', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(cfg),
  });
  await checkResponse(r);
}

export async function listPresets(): Promise<string[]> {
  const r = await fetch('/api/presets');
  if (!r.ok) return [];
  return r.json();
}

export async function fetchPreset(name: string): Promise<Config> {
  const r = await fetch(`/api/presets/${encodeURIComponent(name)}`);
  await checkResponse(r);
  return r.json();
}

export async function savePreset(name: string, cfg: Config): Promise<void> {
  const r = await fetch(`/api/presets/${encodeURIComponent(name)}/save`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(cfg),
  });
  await checkResponse(r);
}

export async function applyPreset(name: string): Promise<void> {
  const r = await fetch(`/api/presets/${encodeURIComponent(name)}/apply`, {
    method: 'POST',
  });
  await checkResponse(r);
}

export async function deletePreset(name: string): Promise<void> {
  const r = await fetch(`/api/presets/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
  await checkResponse(r);
}
