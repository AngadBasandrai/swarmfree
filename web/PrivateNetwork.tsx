import { useState } from 'react';
import { Cable, FolderOpen, X } from 'lucide-react';
import { api, chooseTlsFile } from './api';

type Settings = { enabled: boolean; port: number; public_origin: string; cert_file: string; key_file: string; advertise_lan: boolean };

export function PrivateNetwork({ onError }: { onError: (message: string) => void }) {
  const [settings, setSettings] = useState<Settings>();
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  async function open() {
    try { setSettings(await api('/private-network')); setSaved(false); }
    catch (e) { onError(String(e)); }
  }
  async function choose(privateKey: boolean) {
    try { const file = await chooseTlsFile(privateKey); if (file) setSettings(s => s && { ...s, [privateKey ? 'key_file' : 'cert_file']: file }); }
    catch (e) { onError(String(e)); }
  }
  async function save() {
    setBusy(true);
    try { await api('/private-network', 'PUT', settings); setSaved(true); }
    catch (e) { onError(String(e)); }
    finally { setBusy(false); }
  }
  return <><button className="secondary" onClick={() => void open()}><Cable size={16} /> Connection settings</button>
    {settings && <div className="modal-backdrop"><section className="modal pairing-modal" role="dialog" aria-modal="true" aria-labelledby="network-title">
      <header><div><div className="eyebrow">PRIVATE DEVICE CONNECTIONS</div><h2 id="network-title">Accept your other devices</h2></div><button className="icon-button" aria-label="Close connection settings" onClick={() => setSettings(undefined)}><X size={20} /></button></header>
      <p>Enable a TLS listener for paired workers. The main workbench stays private on this computer. A self-signed certificate works when workers use its trusted CA file.</p>
      <label className="checkbox"><input type="checkbox" checked={settings.enabled} onChange={e => { setSettings({ ...settings, enabled: e.target.checked }); setSaved(false); }} /> Accept private workers</label>
      <label className="field"><span>Address workers will connect to</span><input placeholder="https://my-computer:8443" value={settings.public_origin} onChange={e => { setSettings({ ...settings, public_origin: e.target.value }); setSaved(false); }} /></label>
      <label className="field"><span>Listening port</span><input type="number" min={1024} max={65535} value={settings.port} onChange={e => { setSettings({ ...settings, port: Number(e.target.value) }); setSaved(false); }} /></label>
      <button className="secondary" onClick={() => { void (async () => { try {const host = new URL(settings.public_origin).hostname; const certificate = await api<{cert_file:string;key_file:string}>('/pairing/certificate','POST',{hostname:host});setSettings({...settings,...certificate});setSaved(false);} catch(e){onError(String(e));} })(); }}>Generate a certificate for this address</button>
      <label className="checkbox"><input type="checkbox" checked={settings.advertise_lan||false} onChange={e=>{setSettings({...settings,advertise_lan:e.target.checked});setSaved(false);}}/>Advertise this coordinator on my LAN</label>
      <label className="field"><span>TLS certificate</span><input readOnly value={settings.cert_file} placeholder="Select a .pem or .crt file" /><button className="secondary" onClick={() => void choose(false)}><FolderOpen size={14} /> Choose certificate</button></label>
      <label className="field"><span>TLS private key</span><input readOnly value={settings.key_file} placeholder="Select a .pem or .key file" /><button className="secondary" onClick={() => void choose(true)}><FolderOpen size={14} /> Choose private key</button></label>
      <button className="primary" disabled={busy} onClick={() => void save()}>{busy ? 'Checking certificate…' : 'Save connection settings'}</button>
      {saved && <p role="status">Saved. Close and reopen Swarm Workbench to apply the listener settings, then pair a device.</p>}
    </section></div>}
  </>;
}
