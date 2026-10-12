import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Plus, Check, Download, Upload, Copy, Trash2, Undo2, Redo2, LayoutGrid, Link2, Search, X, Network } from 'lucide-react';
import { api, download } from './api';
import { Graph } from './Graph';
import { AdvancedNodeControls } from './Studio';
import { autoLayout, categories, graphErrors, repairSources, kindName } from './architectureTools';
import type { Architecture, AgentSpec, Snapshot } from './types';

type Action = <T>(fn: () => Promise<T>) => Promise<T | undefined>;
type History = { past: Architecture[]; present: Architecture; future: Architecture[] };
type Preview = { expanded_nodes: number; child_calls: number; model_calls: number; worst_model_calls: number; max_depth: number; note: string };
const id = () => 'custom_' + crypto.randomUUID().replaceAll('-', '').slice(0, 16);

export function Architectures({ snapshot: s, action, refresh }: { snapshot: Snapshot; action: Action; refresh: () => Promise<void> }) {
  const [history, setHistory] = useState<History>(() => ({ past: [], present: structuredClone(s.architectures[0]), future: [] }));
  const arch = history.present;
  const [baseline, setBaseline] = useState(() => JSON.stringify(arch));
  const [selected, setSelected] = useState(arch.nodes[0].id);
  const [search, setSearch] = useState(''), [category, setCategory] = useState('all');
  const [from, setFrom] = useState(arch.nodes[0].id), [to, setTo] = useState(arch.nodes[1]?.id || '');
  const [notice, setNotice] = useState(''), [pending, setPending] = useState<Architecture>();
  const [preview, setPreview] = useState<Preview>(), [saving, setSaving] = useState(false), [graphVersion, setGraphVersion] = useState(0);
  const input = useRef<HTMLInputElement>(null), lastEdit = useRef({ group: '', time: 0 });
  const inspector = useRef<HTMLElement>(null);
  const node = arch.nodes.find(n => n.id === selected) || arch.nodes[0];
  const dirty = JSON.stringify(arch) !== baseline, errors = graphErrors(arch);
  useEffect(() => { if (inspector.current) inspector.current.scrollTop = 0; }, [selected, arch.id]);
  const available = s.architectures.filter(a => (a.id === arch.id) || ((category === 'all' || (category === 'custom' ? !a.builtin : (a.category || 'general') === category)) && (a.name + ' ' + a.description).toLowerCase().includes(search.toLowerCase())));

  function mutate(next: Architecture, group = '') {
    const now = Date.now(), coalesce = !!group && lastEdit.current.group === group && now - lastEdit.current.time < 800;
    lastEdit.current = { group, time: now };
    setHistory(h => ({ past: coalesce ? h.past : [...h.past, h.present].slice(-80), present: next, future: [] }));
    setPreview(undefined); setNotice('');
  }
  function load(value: Architecture, saved = true) {
    const copy = structuredClone(value);
    setHistory({ past: [], present: copy, future: [] }); setBaseline(saved ? JSON.stringify(copy) : '');
    setSelected(copy.nodes[0].id); setFrom(copy.nodes[0].id); setTo(copy.nodes[1]?.id || '');
    setPreview(undefined); setNotice(''); setPending(undefined); lastEdit.current = { group: '', time: 0 };
    setGraphVersion(v => v + 1);
  }
  function choose(value: Architecture) { if (dirty) setPending(value); else load(value, s.architectures.some(a => a.id === value.id)); }
  function undo() {
    setHistory(h => h.past.length ? { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] } : h);
    lastEdit.current.group = ''; setPreview(undefined); setNotice('');
  }
  function redo() {
    setHistory(h => h.future.length ? { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) } : h);
    lastEdit.current.group = ''; setPreview(undefined); setNotice('');
  }
  function updateNode(changes: Partial<AgentSpec>, group = '') {
    mutate({ ...arch, nodes: arch.nodes.map(n => n.id === node.id ? { ...n, ...changes } : n) }, group ? node.id + group : '');
  }
  function connect(source: string, target: string) {
    if (!source || !target) return;
    if (arch.edges.some(e => e.source === source && e.target === target)) { setNotice('These agents are already connected.'); return; }
    const next = { ...arch, edges: [...arch.edges, { source, target }] };
    const cycle = graphErrors(next).find(e => e.includes('cycle'));
    if (cycle) { setNotice(cycle); return; }
    mutate(next);
  }
  function removeEdge(source: string, target: string) {
    mutate(repairSources({ ...arch, edges: arch.edges.filter(e => e.source !== source || e.target !== target) }));
  }
  function add(duplicate = false) {
    const next: AgentSpec = duplicate ? { ...structuredClone(node), id: 'agent_' + crypto.randomUUID().slice(0, 8), name: (node.name + ' copy').slice(0, 80), x: node.x, y: node.y + 220 } : { id: 'agent_' + crypto.randomUUID().slice(0, 8), name: 'New agent', role: 'Describe this agent’s role', instructions: '', x: 300, y: arch.nodes.length * 100 };
    const edges = duplicate ? [...arch.edges, ...arch.edges.filter(e => e.target === node.id).map(e => ({ ...e, target: next.id }))] : arch.edges;
    mutate({ ...arch, nodes: [...arch.nodes, next], edges }); setSelected(next.id);
  }
  async function save(): Promise<Architecture | undefined> {
    if (errors.length || saving) return;
    setSaving(true);
    try {
      const value = { ...arch, id: arch.builtin ? id() : arch.id, builtin: false };
      const result = await action(async () => { const saved = await api<Architecture>('/architectures', 'POST', value); await refresh(); return saved; });
      if (result) { load(result); setNotice('Architecture saved. Select it when you launch a mission.'); }
      return result;
    } finally { setSaving(false); }
  }
  function shortcuts(e: KeyboardEvent<HTMLDivElement>) {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (e.key.toLowerCase() === 's') { e.preventDefault(); void save(); return; }
    if ((e.target as HTMLElement).closest('input, textarea, select, [contenteditable]')) return;
    if (e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); }
    if (e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); }
  }
  return <div className="page architectures-page architecture-workspace" onKeyDown={shortcuts}>
    <div className="page-heading"><div><div className="eyebrow">TEAM DESIGN</div><h1>Shape how your swarm works.</h1><p>Start with a pattern. Edit the roles, connect the work, and make it yours.</p></div><button className="secondary" onClick={() => choose({ id: id(), name: 'Untitled team', description: '', builtin: false, category: 'custom', nodes: [{ id: 'worker', name: 'Worker', role: 'Complete the task', instructions: '', x: 0, y: 0 }], edges: [] })}><Plus size={15}/> New team</button><button className="primary" disabled={!!errors.length || saving} onClick={() => void save()}>{saving ? 'Saving…' : arch.builtin ? 'Save custom copy' : 'Save architecture'}<Check size={15}/></button></div>
    {pending && <div className="architecture-notice" role="status"><span>This team has unsaved edits. Save them or discard them before switching.</span><button className="secondary" onClick={() => void save().then(saved => { if (saved) load(pending, s.architectures.some(a => a.id === pending.id)); })}>Save then switch</button><button className="secondary" onClick={() => load(pending, s.architectures.some(a => a.id === pending.id))}>Discard changes</button><button className="text-button" onClick={() => setPending(undefined)}>Keep editing</button></div>}
    <section className="architecture-library" aria-label="Architecture library">
      <label className="architecture-search"><Search size={16}/><input aria-label="Search architectures" placeholder="Find a pattern or team…" value={search} onChange={e => setSearch(e.target.value)}/></label>
      <select aria-label="Architecture category" value={category} onChange={e => setCategory(e.target.value)}>{categories.map(c => <option key={c} value={c}>{c === 'all' ? 'All patterns' : c[0].toUpperCase() + c.slice(1)}</option>)}</select>
      <select aria-label="Architecture template" value={s.architectures.some(a => a.id === arch.id) ? arch.id : ''} onChange={e => { const value = s.architectures.find(a => a.id === e.target.value); if (value) choose(value); }}><option value="" disabled>New / imported team</option>{available.map(a => <option key={a.id} value={a.id}>{a.name}{a.builtin ? ' · preset' : ' · custom'}</option>)}</select>
      <span className="tag">{s.architectures.filter(a => a.builtin).length} presets</span>
    </section>
    <div className="architecture-definition"><label className="field"><span>Architecture name</span><input aria-label="Architecture name" value={arch.name} maxLength={80} onChange={e => mutate({ ...arch, name: e.target.value }, 'name')}/></label><label className="field"><span>Description</span><input aria-label="Architecture description" value={arch.description} maxLength={400} onChange={e => mutate({ ...arch, description: e.target.value }, 'description')}/></label><span className="tag">{dirty ? 'Unsaved changes' : arch.builtin ? 'Built-in preset' : 'Saved'}</span></div>
    <div className="architecture-edit-actions"><div><button className="secondary" disabled={!history.past.length} onClick={undo} title="Ctrl+Z outside text fields"><Undo2 size={15}/> Undo</button><button className="secondary" disabled={!history.future.length} onClick={redo} title="Ctrl+Shift+Z outside text fields"><Redo2 size={15}/> Redo</button><button className="secondary" disabled={!!errors.length} onClick={() => { mutate(autoLayout(arch)); setGraphVersion(v => v+1); }}><LayoutGrid size={15}/> Auto layout</button></div><div><button className="secondary" disabled={arch.nodes.length >= 16} onClick={() => add()}><Plus size={15}/> Add agent</button><button className="icon-button" aria-label="Export architecture" title="Export JSON" onClick={() => download(`${arch.id}.json`, arch)}><Download size={17}/></button><button className="icon-button" aria-label="Import architecture" title="Import JSON" onClick={() => input.current?.click()}><Upload size={17}/></button></div></div>
    <input type="file" accept=".json,application/json" className="hidden" ref={input} onChange={e => {
      const file = e.target.files?.[0]; e.target.value = '';
      if (file) void action(async () => {
        if (file.size > 250000) throw new Error('Architecture file is too large');
        const value = JSON.parse(await file.text()) as Architecture;
        if (!Array.isArray(value.nodes) || !Array.isArray(value.edges)) throw new Error('Architecture JSON needs nodes and edges');
        const imported = { ...value, id: id(), builtin: false }, problems = graphErrors(imported);
        if (problems.length) throw new Error(problems.join(' '));
        const checked = await api<Preview>('/architectures/preview', 'POST', imported);
        if (dirty) { setPending(imported); return; }
        load(imported, false); setPreview(checked);
      });
    }}/>
    {(notice || errors.length > 0) && <div className={`architecture-notice ${errors.length ? 'invalid' : ''}`} role={errors.length ? 'alert' : 'status'}>{errors.length ? errors.join(' ') : notice}</div>}
    <div className="architecture-editor architecture-editor-v2">
      <aside className="architecture-agent-list"><header><span className="eyebrow">TEAM</span><small>{arch.nodes.length} / 16</small></header>{arch.nodes.map(n => <button key={n.id} aria-label={`Select agent ${n.name}`} aria-pressed={node.id === n.id} className={node.id === n.id ? 'selected' : ''} onClick={() => setSelected(n.id)}><Network size={15}/><span><strong>{n.name}</strong><small>{kindName(n.kind)}</small></span></button>)}</aside>
      <div className="architecture-canvas"><div className="editor-graph"><Graph key={graphVersion} architecture={arch} selected={node.id} editable onSelect={setSelected} onMove={(id, x, y) => mutate({ ...arch, nodes: arch.nodes.map(n => n.id === id ? { ...n, x, y } : n) })} onConnect={c => { if (c.source && c.target) connect(c.source, c.target); }} onRemoveEdge={removeEdge}/></div><div className="architecture-connect"><label><span>From</span><select aria-label="Connect from" value={arch.nodes.some(n => n.id === from) ? from : ''} onChange={e => setFrom(e.target.value)}><option value="">Choose agent</option>{arch.nodes.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}</select></label><ArrowLabel/><label><span>To</span><select aria-label="Connect to" value={arch.nodes.some(n => n.id === to) ? to : ''} onChange={e => setTo(e.target.value)}><option value="">Choose agent</option>{arch.nodes.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}</select></label><button className="secondary" disabled={!from || !to} onClick={() => connect(from, to)}><Link2 size={14}/> Connect agents</button></div></div>
      <aside ref={inspector} className="node-editor"><div className="node-editor-heading"><div><div className="eyebrow">NODE DETAILS</div><code>{node.id}</code></div><button className="icon-button" disabled={arch.nodes.length >= 16} aria-label="Duplicate agent" title="Duplicate with its incoming connections" onClick={() => add(true)}><Copy size={16}/></button></div><label className="field"><span>Name</span><input aria-label="Agent name" value={node.name} onChange={e => updateNode({ name: e.target.value }, ':name')} maxLength={80}/></label><label className="field"><span>Role</span><input aria-label="Agent role" value={node.role} onChange={e => updateNode({ role: e.target.value }, ':role')} maxLength={160}/></label><AdvancedNodeControls architecture={arch} architectures={s.architectures} node={node} onChange={updateNode}/><label className="field"><span>{node.kind === 'recursive' ? 'Base case instructions' : 'Instructions'}</span><textarea aria-label="Agent instructions" value={node.instructions} onChange={e => updateNode({ instructions: e.target.value }, ':instructions')} maxLength={12000}/></label><div className="node-connections"><div className="eyebrow">CONNECTIONS</div>{arch.edges.filter(e => e.source === node.id || e.target === node.id).map(e => <div key={e.source + e.target}><span>{arch.nodes.find(n => n.id === e.source)?.name} → {arch.nodes.find(n => n.id === e.target)?.name}</span><button className="icon-button" aria-label={`Remove connection ${e.source} to ${e.target}`} onClick={() => removeEdge(e.source, e.target)}><X size={13}/></button></div>)}{!arch.edges.some(e => e.source === node.id || e.target === node.id) && <p>This agent runs independently.</p>}</div><button className="danger-button" disabled={arch.nodes.length === 1} onClick={() => { const nodes = arch.nodes.filter(n => n.id !== node.id); mutate(repairSources({ ...arch, nodes, edges: arch.edges.filter(e => e.source !== node.id && e.target !== node.id) })); setSelected(nodes[0].id); }}><Trash2 size={14}/> Remove agent</button></aside>
    </div>
    <section className="architecture-run-settings"><div><h2>Execution settings</h2><p>Independent roots run in parallel. Joins wait for upstream work. Every nested team uses the root mission’s budgets.</p></div><label className="field"><span>Maximum revision rounds</span><input aria-label="Revision rounds" type="number" min={1} max={5} value={arch.max_rounds || 1} onChange={e => mutate({ ...arch, max_rounds: Number(e.target.value) })}/></label><label className="field"><span>Stop rounds when terminal output contains</span><input aria-label="Revision stop condition" value={arch.stop_when_contains || ''} maxLength={120} onChange={e => mutate({ ...arch, stop_when_contains: e.target.value }, 'stop')}/></label><button className="secondary" disabled={!!errors.length} onClick={() => void action(async () => setPreview(await api<Preview>('/architectures/preview', 'POST', arch)))}>Check architecture</button></section>
    {preview && <div className="architecture-preview" role="status"><strong>Valid architecture</strong><span>{preview.expanded_nodes} expanded nodes</span><span>{preview.child_calls} possible child calls</span><span>Depth {preview.max_depth}</span><span>Up to {preview.worst_model_calls} model calls before retries / tools</span><p>{preview.note}</p></div>}
    <p className="inline-note">Drag nodes to arrange the team, or use Auto layout. Connect with the controls below the canvas or drag between handles. Recursive nodes call a saved team, then solve directly at the depth limit or stop condition. Dependency edges stay acyclic.</p>
  </div>;
}

function ArrowLabel() { return <span className="connect-arrow" aria-hidden="true">→</span>; }
