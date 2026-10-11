import { useMemo } from 'react';
import { Background, Controls, Handle, Position, ReactFlow, type NodeProps, type Node, type Connection } from '@xyflow/react';
import { Circle, Check, LoaderCircle, AlertCircle, Pause, Bot } from 'lucide-react';
import type { Architecture, Agent } from './types';

type AgentNode = Node<{ name: string; role: string; status: string; model: string }, 'agent'>;
export function StatusIcon({ status, size = 14 }: { status: string; size?: number }) {
  if (status === 'done' || status === 'completed') return <Check size={size} />;
  if (status === 'running' || status === 'chatting') return <LoaderCircle size={size} className="spin" />;
  if (status === 'failed' || status === 'interrupted') return <AlertCircle size={size} />;
  if (status === 'paused') return <Pause size={size} />;
  return <Circle size={size} />;
}
function AgentCard({ data, selected }: NodeProps<AgentNode>) {
  return <div className={`graph-agent ${selected ? 'selected' : ''} state-${data.status}`}>
    <Handle type="target" position={Position.Left} />
    <div className="graph-agent-top"><span className="agent-glyph"><Bot size={17} /></span><span className={`status-text ${data.status}`}><StatusIcon status={data.status} /> {data.status}</span></div>
    <strong>{data.name}</strong><p>{data.role}</p>
    <div className="graph-agent-model">{data.model || 'Assigned when ready'}</div>
    <Handle type="source" position={Position.Right} />
  </div>;
}
const nodeTypes = { agent: AgentCard };
export function Graph({ architecture, agents = [], selected, onSelect, editable = false, onConnect, onMove, onRemoveEdge }: {
  architecture: Architecture; agents?: Agent[]; selected?: string; onSelect?: (id: string) => void; editable?: boolean;
  onConnect?: (connection: Connection) => void; onMove?: (id: string, x: number, y: number) => void; onRemoveEdge?: (source: string, target: string) => void;
}) {
  const nodes: AgentNode[] = useMemo(() => architecture.nodes.map(n => {
    const a = agents.find(a => a.node_id === n.id);
    return { id: n.id, type: 'agent', position: { x: n.x, y: n.y }, selected: n.id === selected, data: { name: n.name, role: n.role, status: a?.status || 'waiting', model: a?.actual_route || (editable ? 'Custom agent' : '') } };
  }), [architecture, agents, selected, editable]);
  const edges = architecture.edges.map(e => ({ ...e, id: e.source + '->' + e.target, type: 'smoothstep', animated: agents.some(a => a.node_id === e.source && a.status === 'running'), style: { stroke: '#59756a', strokeWidth: 1.5 } }));
  return <ReactFlow key={architecture.id} nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: .2 }} minZoom={.3} maxZoom={1.6}
    nodesDraggable={editable} nodesConnectable={editable} elementsSelectable onNodeClick={(_, n) => onSelect?.(n.id)}
    onConnect={onConnect} onNodeDrag={(_, n) => onMove?.(n.id, n.position.x, n.position.y)}
    onEdgeDoubleClick={(_, e) => editable && onRemoveEdge?.(e.source, e.target)} proOptions={{ hideAttribution: false }}>
    <Background color="#c4cec7" gap={22} size={1} /><Controls showInteractive={false} />
  </ReactFlow>;
}
