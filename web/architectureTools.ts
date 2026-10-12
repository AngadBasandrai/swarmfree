import type { Architecture } from './types';

export function ancestors(arch: Architecture, id: string): Set<string> {
  const seen = new Set<string>(), pending = [id];
  while (pending.length) {
    const current = pending.pop();
    for (const edge of arch.edges) if (edge.target === current && !seen.has(edge.source)) {
      seen.add(edge.source); pending.push(edge.source);
    }
  }
  return seen;
}

export function graphErrors(arch: Architecture): string[] {
  const errors: string[] = [], ids = new Set(arch.nodes.map(n => n.id));
  if (!arch.name.trim()) errors.push('Give this architecture a name.');
  if (!arch.nodes.length || arch.nodes.length > 16) errors.push('Teams need between 1 and 16 nodes.');
  if (ids.size !== arch.nodes.length) errors.push('Every node needs a unique ID.');
  if (ids.has('results')) errors.push('The node ID results is reserved.');
  if (!/^[a-zA-Z][a-zA-Z0-9_-]{0,47}$/.test(arch.id)) errors.push('Use a valid architecture ID.');
  for (const node of arch.nodes) {
    if (!/^[a-zA-Z][a-zA-Z0-9_-]{0,47}$/.test(node.id) || !node.name.trim() || !node.role.trim()) errors.push('Each node needs a valid ID, name and role.');
    if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) errors.push('Node positions must be finite numbers.');
    if (node.kind === 'subswarm' && !node.subswarm_id) errors.push(`${node.name}: choose a child team.`);
    if (['recursive','fanout'].includes(node.kind||'') && (!Number.isInteger(node.max_depth ?? 2) || (node.max_depth ?? 2) < 1 || (node.max_depth ?? 2) > 4)) errors.push(`${node.name}: recursion depth must be 1–4.`);
    const upstream = ancestors(arch, node.id);
    if (upstream.has(node.id)) errors.push('A connection creates a dependency cycle. Use a recursive team node or revision rounds.');
    if (node.task_source && !upstream.has(node.task_source)) errors.push(`${node.name}: the task source must be upstream.`);
    if (node.run_if?.source && !upstream.has(node.run_if.source)) errors.push(`${node.name}: the condition must inspect an upstream node.`);
  }
  const pairs = new Set<string>();
  const boundNames=new Set<string>();
  for (const edge of arch.edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) errors.push('A connection refers to a missing node.');
    const pair = edge.source + '->' + edge.target;
    if (pairs.has(pair)) errors.push('A connection is duplicated.');
    pairs.add(pair);
    for(const binding of edge.bindings||[]){
      if(!/^[A-Za-z][A-Za-z0-9_]{0,95}$/.test(binding.target_name))errors.push('Typed inputs need a valid field name.');
      if(binding.source_path&& !/^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$/.test(binding.source_path))errors.push('Output selectors use dot-separated field names or array indexes.');
      const key=edge.target+':'+binding.target_name;
      if(boundNames.has(key))errors.push('An input field can have only one connection binding.');
      boundNames.add(key);
    }
  }
  if(arch.output_ports?.some(p=>!ids.has(p.source)))errors.push('A named output refers to a missing node.');
  if (!Number.isInteger(arch.max_rounds ?? 1) || (arch.max_rounds ?? 1) < 1 || (arch.max_rounds ?? 1) > 5) errors.push('Revision rounds must be 1–5.');
  return [...new Set(errors)];
}

export function repairSources(arch: Architecture): Architecture {
  return { ...arch, nodes: arch.nodes.map(node => {
    const upstream = ancestors(arch, node.id);
    return { ...node, task_source: upstream.has(node.task_source || '') ? node.task_source : '', run_if: upstream.has(node.run_if?.source || '') ? node.run_if : {} };
  }), channels: arch.channels?.map(c => ({ ...c, members: c.members.filter(id => arch.nodes.some(n => n.id === id)) })).filter(c => c.members.length > 1) };
}

export function autoLayout(arch: Architecture): Architecture {
  if (graphErrors(arch).some(e => e.includes('cycle'))) return arch;
  const levels = new Map<string, number>();
  function level(id: string): number {
    if (levels.has(id)) return levels.get(id)!;
    const parents = arch.edges.filter(e => e.target === id).map(e => e.source);
    const value = parents.length ? Math.max(...parents.map(level)) + 1 : 0;
    levels.set(id, value); return value;
  }
  const rows = new Map<number, number>();
  return { ...arch, nodes: arch.nodes.map(node => {
    const column = level(node.id), row = rows.get(column) || 0;
    rows.set(column, row + 1);
    return { ...node, x: column * 300, y: row * 220 };
  }) };
}

export const categories = ['all', 'general', 'coding', 'research', 'decision', 'recursive', 'custom'];
export const kindName = (kind?: string) => ({ vote: 'Vote', subswarm: 'Child team', recursive: 'Recursive team',fanout:'Dynamic fanout',router:'Model router',condition:'Condition',join:'Join' }[kind || ''] || 'Model agent');
