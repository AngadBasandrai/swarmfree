import { invoke, isTauri } from '@tauri-apps/api/core';
import { writeText } from '@tauri-apps/plugin-clipboard-manager';
import { openUrl } from '@tauri-apps/plugin-opener';
import { isPermissionGranted, requestPermission, sendNotification } from '@tauri-apps/plugin-notification';

export const desktop = isTauri();

export function connectDesktop() { return invoke<void>('backend_ready'); }
export function chooseTlsFile(privateKey: boolean) { return invoke<string | null>('choose_tls_file', { privateKey }); }
export function openAppData() { return invoke<void>('open_app_data'); }
export function chooseProjectFolder() { return invoke<string | null>('choose_project_folder'); }
export function chooseLocalFile(kind: 'gguf'|'runtime'|'backup') { return invoke<string|null>('choose_local_file',{kind}); }
export async function exportBackup(id:string,path:string) {
  if(desktop)return invoke<boolean>('export_backup',{name:path.split(/[\\/]/).pop()});
  const response=await fetch('/api/next/backup/'+id+'/download',{credentials:'same-origin'});
  if(!response.ok)throw new Error('Cannot download this backup');
  const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=path.split(/[\\/]/).pop()||'backup.swarmbak';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return true;
}
export function checkUpdate(endpoint: string, publicKey: string) { return invoke<{ version: string; notes: string } | null>('check_update', { endpoint, publicKey }); }
export function installUpdate(endpoint: string, publicKey: string) { return invoke<void>('install_update', { endpoint, publicKey }); }

export async function enableNotifications() {
  if (!desktop) return false;
  const granted = await isPermissionGranted() || (await requestPermission()) === 'granted';
  if (granted) localStorage.setItem('swarm-notifications', 'enabled');
  return granted;
}
export async function notify(title: string, body: string) {
  if (desktop && localStorage.getItem('swarm-notifications') === 'enabled' && await isPermissionGranted()) sendNotification({title,body});
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  if (desktop) {
    const response = await invoke<{ status: number; data: { detail?: unknown } }>('backend_request', { path: '/api' + path, method, body: body ?? null });
    if (response.status >= 400) throw new Error(errorDetail(response.data.detail, response.status));
    return response.data as T;
  }
  const r = await fetch('/api' + path, { method, credentials: 'same-origin', headers: body === undefined ? {} : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json();
  if (!r.ok) {
    const detail = data.detail;
    throw new Error(errorDetail(detail, r.status));
  }
  return data as T;
}
function errorDetail(detail: unknown, status: number) {
  return typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((e: { msg: string }) => e.msg).join('; ') : `Request failed (${status})`;
}
function showError(error: unknown) { window.dispatchEvent(new CustomEvent('swarm:error', { detail: error instanceof Error ? error.message : String(error) })); }

export function copyText(text: string) { return desktop ? writeText(text) : navigator.clipboard.writeText(text); }

export function installDesktopLinks() {
  if (!desktop) return;
  document.addEventListener('click', e => {
    const anchor = (e.target as Element).closest?.('a[href]');
    if (anchor instanceof HTMLAnchorElement && anchor.href.startsWith('https://')) {
      e.preventDefault(); void openUrl(anchor.href).catch(showError);
    }
  });
}

export function download(name: string, value: unknown) {
  if (desktop) { void invoke('export_json', { name, value }).catch(showError); return; }
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
