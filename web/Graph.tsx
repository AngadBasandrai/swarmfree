import { useEffect, useMemo, useState } from 'react';
import { Background, Controls, Handle, Position, ReactFlow, useNodesState, type NodeProps, type Node, type Connection, type ReactFlowInstance } from '@xyflow/react';
import { Circle, Check, LoaderCircle, AlertCircle, Pause, Bot, GitBranch, Repeat2, Vote } from 'lucide-react';
import { kindName } from './architectureTools';
import type { Architecture, Agent } from './types';

type AgentNode = Node<{ name: string; role: string; status: string; model: string; kind?: string; depth?: number }, 'agent'>;
export function StatusIcon({ status, size = 14 }: { status: string; size?: number }) {
  if (status === 'done' || status === 'completed') return <Check size={size} />;
  if (status === 'running' || status === 'chatting') return <LoaderCircle size={size} className="spin" />;
  if (status === 'failed' || status === 'interrupted') return <AlertCircle size={size} />;
  if (['paused','held','waiting'].includes(status)) return <Pause size={size} />;
  return <Circle size={size} />;
}
function AgentCard({ data, selected }: NodeProps<AgentNode>) {
  const Icon = data.kind === 'recursive' ? Repeat2 : data.kind === 'subswarm' ? GitBranch : data.kind === 'vote' ? Vote : Bot;
  return <div className={`graph-agent ${selected ? 'selected' : ''} state-${data.status}`}>
    <Handle type="target" position={Position.Left} />
    <div className="graph-agent-top"><span className="agent-glyph"><Icon size={17} /></span><span className={`status-text ${data.status}`}><StatusIcon status={data.status} /> {data.status}</span></div>
    <strong>{data.name}</strong><p>{data.role}</p>
    <div className="graph-agent-model">{data.model || 'Assigned when ready'}</div>
    <Handle type="source" position={Position.Right} />
  </div>;
}
const nodeTypes = { agent: AgentCard };
const emptyAgents: Agent[] = [];
export function Graph({ architecture, agents = emptyAgents, selected, onSelect, editable = false, onConnect, onMove, onRemoveEdge, onDropNode }: {
  architecture: Architecture; agents?: Agent[]; selected?: string; onSelect?: (id: string) => void; editable?: boolean;
  onConnect?: (connection: Connection) => void; onMove?: (id: string, x: number, y: number) => void; onRemoveEdge?: (source: string, target: string) => void;
  onDropNode?: (value:string,x:number,y:number)=>void;
}) {
  const [flow,setFlow]=useState<ReactFlowInstance<AgentNode>>();
  const nodes: AgentNode[] = useMemo(() => architecture.nodes.map(n => {
    const a = agents.find(a => a.node_id === n.id);
    const label = n.kind === 'recursive' ? `Recursive · depth ${n.max_depth ?? 2}${n.recursion_leaf ? ' · base case' : ''}` : kindName(n.kind);
    return { id: n.id, type: 'agent', position: { x: n.x, y: n.y }, selected: n.id === selected, data: { name: n.name, role: n.role, status: a?.status || 'waiting', kind: n.kind, model: a?.actual_route || (editable || n.kind === 'recursive' || n.kind === 'subswarm' ? label : '') } };
  }), [architecture, agents, selected, editable]);
  const [displayNodes, setDisplayNodes, onNodesChange] = useNodesState<AgentNode>(nodes);
  useEffect(() => { setDisplayNodes(nodes); }, [nodes, setDisplayNodes]);
  const edges = architecture.edges.map(e => ({ ...e, label:e.bindings?.length ? e.bindings.map(b=>b.target_name).join(', ') : undefined, id: e.source + '->' + e.target, type: 'smoothstep', animated: agents.some(a => a.node_id === e.source && a.status === 'running'), style: { stroke: '#59756a', strokeWidth: 1.5 } }));
  return <ReactFlow key={architecture.id} nodes={displayNodes} onNodesChange={onNodesChange} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: .2 }} minZoom={.3} maxZoom={1.6} deleteKeyCode={null}
    nodesDraggable={editable} nodesConnectable={editable} elementsSelectable onNodeClick={(_, n) => onSelect?.(n.id)}
    onInit={setFlow} onDragOver={e=>{if(editable){e.preventDefault();e.dataTransfer.dropEffect='copy';}}} onDrop={e=>{if(!editable||!flow)return;e.preventDefault();const value=e.dataTransfer.getData('application/swarm-node');if(value){const p=flow.screenToFlowPosition({x:e.clientX,y:e.clientY});onDropNode?.(value,p.x,p.y);}}}
    onConnect={onConnect} onNodeDragStop={(_, n) => onMove?.(n.id, n.position.x, n.position.y)}
    onEdgeDoubleClick={(_, e) => editable && onRemoveEdge?.(e.source, e.target)} proOptions={{ hideAttribution: false }}>
    <Background color="#c4cec7" gap={22} size={1} /><Controls showInteractive={false} />
  </ReactFlow>;
}
