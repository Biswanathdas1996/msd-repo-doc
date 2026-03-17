import { useMemo, useCallback, useEffect, useState, useRef } from 'react';
import {
  ReactFlow,
  MiniMap,
  Controls,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  NodeProps,
  MarkerType,
  Panel,
  useReactFlow,
  ReactFlowProvider,
  EdgeProps,
  getBezierPath,
  EdgeLabelRenderer,
  BaseEdge,
} from '@xyflow/react';
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } from 'd3-force';
import type { SimulationNodeDatum, SimulationLinkDatum } from 'd3-force';
import '@xyflow/react/dist/style.css';
import { KnowledgeGraph } from '@workspace/api-client-react';
import { X, Database, Zap, Puzzle, GitBranch, Hash, ArrowRight } from 'lucide-react';

interface KnowledgeGraphViewerProps {
  data: KnowledgeGraph;
}

const NODE_RADIUS = 44;

const COLORS = {
  Entity:   { fill: '#4C8EDA', glow: 'rgba(76,142,218,0.55)',  border: '#7DB3EE', text: '#fff', dim: 'rgba(76,142,218,0.15)'  },
  Workflow: { fill: '#C990C0', glow: 'rgba(201,144,192,0.55)', border: '#DDB6DA', text: '#fff', dim: 'rgba(201,144,192,0.15)' },
  Plugin:   { fill: '#57C278', glow: 'rgba(87,194,120,0.55)',  border: '#83D699', text: '#fff', dim: 'rgba(87,194,120,0.15)'  },
};

type NodeType = keyof typeof COLORS;

interface NodeData {
  label: string;
  nodeType: NodeType;
  details: Record<string, unknown>;
  selected?: boolean;
}

function CircleNode({ data, selected }: NodeProps) {
  const { label, nodeType, details } = data as NodeData;
  const c = COLORS[nodeType] ?? COLORS.Entity;
  const Icon = nodeType === 'Entity' ? Database : nodeType === 'Workflow' ? Zap : Puzzle;

  return (
    <>
      <Handle type="target" position={Position.Top}    style={{ opacity: 0, pointerEvents: 'none' }} />
      <Handle type="target" position={Position.Left}   style={{ opacity: 0, pointerEvents: 'none' }} />
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
      <Handle type="source" position={Position.Right}  style={{ opacity: 0, pointerEvents: 'none' }} />

      <div
        style={{
          width: NODE_RADIUS * 2,
          height: NODE_RADIUS * 2,
          borderRadius: '50%',
          background: `radial-gradient(circle at 35% 35%, ${c.border}cc, ${c.fill})`,
          border: `2.5px solid ${selected ? '#fff' : c.border}`,
          boxShadow: selected
            ? `0 0 0 3px ${c.fill}88, 0 0 28px 8px ${c.glow}, inset 0 1px 0 rgba(255,255,255,0.2)`
            : `0 0 16px 4px ${c.glow}, inset 0 1px 0 rgba(255,255,255,0.15)`,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 2,
          cursor: 'pointer',
          transition: 'box-shadow 0.2s, border-color 0.2s',
          userSelect: 'none',
          position: 'relative',
        }}
      >
        <Icon size={13} style={{ color: 'rgba(255,255,255,0.75)', flexShrink: 0 }} />
        <span style={{
          color: '#fff',
          fontSize: 10,
          fontWeight: 700,
          textAlign: 'center',
          lineHeight: 1.2,
          maxWidth: NODE_RADIUS * 2 - 14,
          overflow: 'hidden',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          wordBreak: 'break-word',
          padding: '0 4px',
        }}>
          {label}
        </span>
        <span style={{
          position: 'absolute',
          bottom: 7,
          fontSize: 8,
          color: 'rgba(255,255,255,0.55)',
          fontWeight: 600,
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
        }}>
          {nodeType}
        </span>
      </div>
    </>
  );
}

const EDGE_STYLES: Record<string, { stroke: string; dash?: string }> = {
  triggers:  { stroke: 'rgba(250,200,60,0.75)' },
  used_in:   { stroke: 'rgba(100,180,255,0.45)', dash: '4 3' },
  invokes:   { stroke: 'rgba(180,120,255,0.55)' },
  has_form:  { stroke: 'rgba(80,220,140,0.45)', dash: '2 4' },
};
const DEFAULT_EDGE_STYLE = { stroke: 'rgba(120,120,180,0.4)' };

function NeoEdge({
  id, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition, data, markerEnd,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  });

  const label    = (data as any)?.label  as string | undefined;
  const relType  = (data as any)?.relType as string | undefined;
  const eStyle   = EDGE_STYLES[relType ?? ''] ?? DEFAULT_EDGE_STYLE;

  return (
    <>
      <BaseEdge
        id={id} path={edgePath} markerEnd={markerEnd}
        style={{
          stroke: eStyle.stroke,
          strokeWidth: relType === 'triggers' ? 2 : 1.5,
          strokeDasharray: eStyle.dash,
        }}
      />
      {label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: 'none',
              background: 'rgba(20,22,40,0.85)',
              color: eStyle.stroke,
              fontSize: 9,
              fontWeight: 600,
              padding: '2px 6px',
              borderRadius: 4,
              border: `1px solid ${eStyle.stroke.replace('0.45', '0.25').replace('0.75', '0.4')}`,
              letterSpacing: '0.03em',
              whiteSpace: 'nowrap',
              backdropFilter: 'blur(4px)',
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { circle: CircleNode };
const edgeTypes = { neo: NeoEdge };

interface SimNode extends SimulationNodeDatum {
  id: string;
}

function buildGraph(data: KnowledgeGraph) {
  const nodes: Array<{ id: string; nodeType: NodeType; details: Record<string, unknown> }> = [];
  const edges: Array<{ id: string; source: string; target: string; label?: string }> = [];
  const seen = new Set<string>();
  const edgeSeen = new Set<string>();

  const addNode = (id: string, type: NodeType, details: Record<string, unknown>) => {
    if (!seen.has(id)) {
      seen.add(id);
      nodes.push({ id, nodeType: type, details });
    }
  };

  const addEdge = (source: string, target: string, relType?: string, label?: string) => {
    const key = `${source}→${target}`;
    if (!edgeSeen.has(key) && source !== target) {
      edgeSeen.add(key);
      const displayLabel = label ?? relType;
      edges.push({ id: `e-${source}-${target}`, source, target, label: displayLabel, relType });
    }
  };

  Object.entries(data.entities ?? {}).forEach(([name, info]: [string, any]) => {
    addNode(name, 'Entity', info);
  });

  Object.entries(data.workflows ?? {}).forEach(([name, info]: [string, any]) => {
    addNode(name, 'Workflow', info);
  });

  Object.entries(data.plugins ?? {}).forEach(([name, info]: [string, any]) => {
    addNode(name, 'Plugin', info);
  });

  (data.relationships ?? []).forEach((rel: any) => {
    if (rel.source && rel.target) addEdge(rel.source, rel.target, rel.type, rel.label);
  });

  Object.entries(data.workflows ?? {}).forEach(([name, info]: [string, any]) => {
    (info.plugins ?? []).forEach((pl: string) => addEdge(name, pl, 'invokes'));
  });

  Object.entries(data.plugins ?? {}).forEach(([name, info]: [string, any]) => {
    if (info.triggerEntity) addEdge(info.triggerEntity, name, 'triggers');
  });

  return { nodes, edges };
}

function computeForceLayout(
  nodeIds: string[],
  rawEdges: Array<{ source: string; target: string }>,
  width: number,
  height: number,
): Record<string, { x: number; y: number }> {
  if (nodeIds.length === 0) return {};

  const simNodes: SimNode[] = nodeIds.map((id, i) => {
    const angle = (i / nodeIds.length) * 2 * Math.PI;
    const r = Math.min(width, height) * 0.3;
    return { id, x: width / 2 + Math.cos(angle) * r, y: height / 2 + Math.sin(angle) * r };
  });

  const idSet = new Set(nodeIds);
  const simLinks: SimulationLinkDatum<SimNode>[] = rawEdges
    .filter(e => idSet.has(e.source) && idSet.has(e.target))
    .map(e => ({ source: e.source as any, target: e.target as any }));

  const sim = forceSimulation<SimNode>(simNodes)
    .force('link',    forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks)
                        .id(d => d.id).distance(200).strength(0.4))
    .force('charge',  forceManyBody<SimNode>().strength(-600))
    .force('center',  forceCenter(width / 2, height / 2))
    .force('collide', forceCollide<SimNode>(NODE_RADIUS + 30))
    .stop();

  for (let i = 0; i < 400; i++) sim.tick();

  return Object.fromEntries(simNodes.map(n => [n.id, { x: n.x ?? 0, y: n.y ?? 0 }]));
}

function DetailsPanel({ nodeId, nodeData, onClose }: {
  nodeId: string;
  nodeData: NodeData;
  onClose: () => void;
}) {
  const { nodeType, details } = nodeData;
  const c = COLORS[nodeType];
  const Icon = nodeType === 'Entity' ? Database : nodeType === 'Workflow' ? Zap : Puzzle;
  const info = details as any;

  return (
    <div style={{
      position: 'absolute', right: 12, top: 12, bottom: 12,
      width: 260, zIndex: 20,
      background: 'rgba(14,16,32,0.97)',
      border: `1px solid ${c.border}55`,
      borderRadius: 12,
      boxShadow: `0 0 30px ${c.glow}, 0 4px 24px rgba(0,0,0,0.6)`,
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
      backdropFilter: 'blur(12px)',
    }}>
      <div style={{
        padding: '14px 16px 12px',
        borderBottom: '1px solid rgba(255,255,255,0.07)',
        background: `linear-gradient(135deg, ${c.fill}22, transparent)`,
        display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8,
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1, minWidth: 0 }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            background: c.fill, color: '#fff', fontSize: 9, fontWeight: 700,
            padding: '3px 8px', borderRadius: 20, letterSpacing: '0.06em',
            textTransform: 'uppercase', alignSelf: 'flex-start', flexShrink: 0,
          }}>
            <Icon size={9} /> {nodeType}
          </span>
          <span style={{ color: '#e2e8f0', fontSize: 14, fontWeight: 700, lineHeight: 1.3, wordBreak: 'break-word' }}>
            {nodeId}
          </span>
        </div>
        <button onClick={onClose} style={{
          background: 'rgba(255,255,255,0.08)', border: 'none', color: 'rgba(255,255,255,0.6)',
          borderRadius: 6, padding: 5, cursor: 'pointer', flexShrink: 0, display: 'flex', alignItems: 'center',
        }}>
          <X size={13} />
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {nodeType === 'Entity' && (
          <>
            {info.fields?.length > 0 && (
              <Section title="Fields" icon={<Hash size={11} />} color={c.fill}>
                {info.fields.map((f: any) => (
                  <Row key={f.name ?? f} label={f.name ?? f} value={f.type} />
                ))}
              </Section>
            )}
            {info.workflows?.length > 0 && (
              <Section title="Connected Workflows" icon={<Zap size={11} />} color={c.fill}>
                {info.workflows.map((w: string) => <Chip key={w} label={w} color={COLORS.Workflow.fill} />)}
              </Section>
            )}
            {info.plugins?.length > 0 && (
              <Section title="Connected Plugins" icon={<Puzzle size={11} />} color={c.fill}>
                {info.plugins.map((p: string) => <Chip key={p} label={p} color={COLORS.Plugin.fill} />)}
              </Section>
            )}
            {!info.fields?.length && !info.workflows?.length && !info.plugins?.length && (
              <EmptyMsg text="No additional properties" />
            )}
          </>
        )}

        {nodeType === 'Workflow' && (
          <>
            {info.trigger && <Row label="Trigger" value={info.trigger} />}
            {info.triggerEntity && <Row label="Trigger Entity" value={info.triggerEntity} />}
            {info.relatedEntities?.length > 0 && (
              <Section title="Uses Entities" icon={<Hash size={11} />} color={c.fill}>
                {info.relatedEntities.map((e: string) => <Chip key={e} label={e} color={COLORS.Entity.fill} />)}
              </Section>
            )}
            {info.steps?.length > 0 && (
              <Section title="Steps" icon={<ArrowRight size={11} />} color={c.fill}>
                {info.steps.map((s: string, i: number) => (
                  <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                    <span style={{ color: c.fill, fontSize: 9, fontWeight: 700, marginTop: 1, flexShrink: 0 }}>{i + 1}.</span>
                    <span style={{ color: '#94a3b8', fontSize: 11, lineHeight: 1.4 }}>{s}</span>
                  </div>
                ))}
              </Section>
            )}
            {info.plugins?.length > 0 && (
              <Section title="Plugins" icon={<Puzzle size={11} />} color={c.fill}>
                {info.plugins.map((p: string) => <Chip key={p} label={p} color={COLORS.Plugin.fill} />)}
              </Section>
            )}
          </>
        )}

        {nodeType === 'Plugin' && (
          <>
            {info.triggerEntity && <Row label="Entity"     value={info.triggerEntity} />}
            {info.operation     && <Row label="Operation"  value={info.operation} />}
            {info.stage         && <Row label="Stage"      value={info.stage} />}
            {info.description   && (
              <Section title="Description" icon={null} color={c.fill}>
                <span style={{ color: '#94a3b8', fontSize: 11, lineHeight: 1.5 }}>{info.description}</span>
              </Section>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Section({ title, icon, color, children }: {
  title: string; icon: React.ReactNode; color: string; children: React.ReactNode;
}) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 6 }}>
        <span style={{ color }}>{icon}</span>
        <span style={{ color: '#64748b', fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          {title}
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>{children}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value?: string }) {
  if (!value) return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
      <span style={{ color: '#64748b', fontSize: 11, flexShrink: 0 }}>{label}</span>
      <span style={{ color: '#94a3b8', fontSize: 11, textAlign: 'right', wordBreak: 'break-word' }}>{value}</span>
    </div>
  );
}

function Chip({ label, color }: { label: string; color: string }) {
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center',
      background: `${color}22`, border: `1px solid ${color}44`,
      borderRadius: 6, padding: '3px 8px',
      color: '#cbd5e1', fontSize: 10, lineHeight: 1.4, wordBreak: 'break-word',
    }}>
      {label}
    </div>
  );
}

function EmptyMsg({ text }: { text: string }) {
  return <span style={{ color: '#475569', fontSize: 11, fontStyle: 'italic' }}>{text}</span>;
}

function GraphInner({ data }: KnowledgeGraphViewerProps) {
  const { fitView } = useReactFlow();
  const containerRef = useRef<HTMLDivElement>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { graphNodes, graphEdges } = useMemo(() => {
    const { nodes: raw, edges: rawEdges } = buildGraph(data);

    const width = 1100, height = 750;
    const positions = computeForceLayout(raw.map(n => n.id), rawEdges, width, height);

    const graphNodes = raw.map(n => ({
      id: n.id,
      type: 'circle',
      position: positions[n.id] ?? { x: 0, y: 0 },
      data: { label: n.id, nodeType: n.nodeType, details: n.details } as NodeData,
      style: { width: NODE_RADIUS * 2, height: NODE_RADIUS * 2 },
    }));

    const graphEdges = rawEdges.map(e => {
      const eStyle = EDGE_STYLES[(e as any).relType ?? ''] ?? DEFAULT_EDGE_STYLE;
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'neo',
        data: { label: e.label, relType: (e as any).relType },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: eStyle.stroke,
          width: 10,
          height: 10,
        },
      };
    });

    return { graphNodes, graphEdges };
  }, [data]);

  const [nodes, setNodes, onNodesChange] = useNodesState(graphNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(graphEdges);

  useEffect(() => {
    setNodes(graphNodes);
    setEdges(graphEdges);
    setTimeout(() => fitView({ padding: 0.18, duration: 400 }), 50);
  }, [graphNodes, graphEdges, setNodes, setEdges, fitView]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: any) => {
    setSelectedId(prev => (prev === node.id ? null : node.id));
  }, []);

  const onPaneClick = useCallback(() => setSelectedId(null), []);

  const selectedNodeData = selectedId
    ? (nodes.find(n => n.id === selectedId)?.data as NodeData | undefined)
    : null;

  const counts = useMemo(() => ({
    entities:  nodes.filter(n => (n.data as NodeData).nodeType === 'Entity').length,
    workflows: nodes.filter(n => (n.data as NodeData).nodeType === 'Workflow').length,
    plugins:   nodes.filter(n => (n.data as NodeData).nodeType === 'Plugin').length,
  }), [nodes]);

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 600, position: 'relative' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        proOptions={{ hideAttribution: true }}
        style={{ background: '#0f101e' }}
        deleteKeyCode={null}
        minZoom={0.15}
        maxZoom={3}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={28}
          size={1.2}
          color="rgba(80,90,140,0.25)"
          style={{ background: '#0f101e' }}
        />

        <Controls
          style={{
            background: 'rgba(14,16,32,0.9)',
            border: '1px solid rgba(80,90,140,0.3)',
            borderRadius: 8,
            boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
          }}
        />

        <MiniMap
          nodeColor={n => {
            const c = COLORS[(n.data as NodeData)?.nodeType as NodeType];
            return c?.fill ?? '#4C8EDA';
          }}
          maskColor="rgba(10,12,24,0.75)"
          style={{
            background: 'rgba(14,16,32,0.9)',
            border: '1px solid rgba(80,90,140,0.3)',
            borderRadius: 8,
          }}
        />

        <Panel position="top-left" style={{ margin: 10 }}>
          <div style={{
            background: 'rgba(14,16,32,0.9)',
            border: '1px solid rgba(80,90,140,0.3)',
            borderRadius: 10,
            padding: '10px 14px',
            backdropFilter: 'blur(10px)',
            boxShadow: '0 4px 20px rgba(0,0,0,0.5)',
          }}>
            <p style={{ color: 'rgba(140,150,200,0.7)', fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 8 }}>
              Graph Overview
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              {([
                ['Entity',   counts.entities,  COLORS.Entity],
                ['Workflow', counts.workflows, COLORS.Workflow],
                ['Plugin',   counts.plugins,   COLORS.Plugin],
              ] as const).map(([label, count, c]) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{
                    width: 10, height: 10, borderRadius: '50%',
                    background: c.fill,
                    boxShadow: `0 0 6px ${c.glow}`,
                    flexShrink: 0,
                  }} />
                  <span style={{ color: '#94a3b8', fontSize: 11 }}>{label}</span>
                  <span style={{
                    marginLeft: 'auto', color: c.fill, fontSize: 11, fontWeight: 700,
                    background: c.dim, borderRadius: 6, padding: '0 6px',
                  }}>
                    {count}
                  </span>
                </div>
              ))}
              <div style={{ borderTop: '1px solid rgba(80,90,140,0.2)', marginTop: 4, paddingTop: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                <GitBranch size={10} style={{ color: 'rgba(120,120,180,0.6)' }} />
                <span style={{ color: '#94a3b8', fontSize: 11 }}>Relationships</span>
                <span style={{
                  marginLeft: 'auto', color: '#7c85cf', fontSize: 11, fontWeight: 700,
                  background: 'rgba(120,120,180,0.1)', borderRadius: 6, padding: '0 6px',
                }}>
                  {edges.length}
                </span>
              </div>
              <div style={{ borderTop: '1px solid rgba(80,90,140,0.2)', marginTop: 4, paddingTop: 6, display: 'flex', flexDirection: 'column', gap: 4 }}>
                <p style={{ color: 'rgba(140,150,200,0.6)', fontSize: 8, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 2 }}>
                  Edge Types
                </p>
                {([
                  ['triggers',  EDGE_STYLES.triggers.stroke,  'solid'],
                  ['used_in',   EDGE_STYLES.used_in.stroke,   'dashed'],
                  ['invokes',   EDGE_STYLES.invokes.stroke,   'solid'],
                ] as const).map(([lbl, color, style]) => (
                  <div key={lbl} style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <svg width={18} height={8} style={{ flexShrink: 0 }}>
                      <line x1={0} y1={4} x2={18} y2={4}
                        stroke={color as string}
                        strokeWidth={style === 'solid' ? 2 : 1.5}
                        strokeDasharray={style === 'dashed' ? '4 3' : undefined}
                      />
                    </svg>
                    <span style={{ color: '#94a3b8', fontSize: 10 }}>{lbl}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Panel>

        {selectedId && selectedNodeData && (
          <Panel position="top-right" style={{ margin: 0, padding: 0, top: 0, right: 0, bottom: 0, position: 'absolute', width: 284 }}>
            <DetailsPanel
              nodeId={selectedId}
              nodeData={selectedNodeData}
              onClose={() => setSelectedId(null)}
            />
          </Panel>
        )}

        {nodes.length === 0 && (
          <Panel position="top-center">
            <div style={{
              marginTop: 60, padding: '20px 28px', textAlign: 'center',
              background: 'rgba(14,16,32,0.9)', border: '1px solid rgba(80,90,140,0.3)',
              borderRadius: 12, color: '#64748b',
            }}>
              <Database size={32} style={{ margin: '0 auto 10px', opacity: 0.4 }} />
              <p style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>No graph data</p>
              <p style={{ fontSize: 12 }}>Upload a solution to visualize its knowledge graph.</p>
            </div>
          </Panel>
        )}
      </ReactFlow>
    </div>
  );
}

export function KnowledgeGraphViewer({ data }: KnowledgeGraphViewerProps) {
  return (
    <ReactFlowProvider>
      <GraphInner data={data} />
    </ReactFlowProvider>
  );
}
