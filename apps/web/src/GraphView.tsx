import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import fcose from 'cytoscape-fcose'
import { ChevronDown } from 'lucide-react'
import { forwardRef, useEffect, useId, useImperativeHandle, useRef, useState } from 'react'
import type { LayoutRequest, LayoutResult } from './graphLayout.worker'
import { PROFILE_PRESENTATION } from './profilePresentation'
import type { GraphEdgeType, GraphResponse, ProfileKind } from './types'

cytoscape.use(fcose)

export interface GraphHandle {
  fit: () => void
  resize: () => void
  reset: () => void
}

interface GraphViewProps {
  graph: GraphResponse
  visibleIds: Set<string>
  selectedId: string | null
  /** Objects the agent actually grounded its last answer in. */
  usedIds?: Set<string>
  onSelect: (id: string) => void
  /** Nodes the user explicitly expanded - their 1-hop neighborhood is revealed and highlighted. */
  expandedIds?: Set<string>
  /** True isolate: hide (not just dim) everything outside the selected/expanded pathway. */
  focusMode?: boolean
  /** Ordered node-id route to highlight for Find Path, or null when none is active. */
  pathIds?: string[] | null
  /** Fired on double-click/double-tap of a node - the caller decides what "expand" means. */
  onExpand?: (id: string) => void
  /** Edge types allowed to ever render; omitted/undefined means no edge-type filter. */
  visibleEdgeTypes?: Set<GraphEdgeType>
}

/** Below this zoom, visually-subordinate relationship-audit nodes disappear entirely (spec 026 LOD). */
const LOD_RELATIONSHIP_ZOOM = 0.55
/** Below this zoom, non-focused node labels are suppressed to cut overview clutter. */
const LOD_LABEL_ZOOM = 0.7
/** Above this node count, layout runs in a Worker instead of synchronously on the
 * main thread (spec 026) - below it, Cytoscape's own fcose layout is already fast. */
export const WORKER_LAYOUT_NODE_THRESHOLD = 300

function cardinalityEndpoint(edge: cytoscape.EdgeSingular, endpoint: 0 | 1) {
  const value = String(edge.data('label') ?? '').split('-to-')[endpoint]
  return value === 'one' || value === 'many' ? value : ''
}

export const physicalRelationshipRule = {
  selector: 'edge[type = "physical_fk"]',
  style: {
    width: 1.4,
    'line-color': '#3EA6B8',
    'target-arrow-color': '#3EA6B8',
    'target-arrow-shape': 'triangle',
    'arrow-scale': 1,
    'source-label': (edge: cytoscape.EdgeSingular) => cardinalityEndpoint(edge, 0),
    'target-label': (edge: cytoscape.EdgeSingular) => cardinalityEndpoint(edge, 1),
    'source-text-offset': 13,
    'target-text-offset': 13,
    color: '#BFEAF1',
    'font-family': 'JetBrains Mono',
    'font-size': 7,
    'text-background-color': '#0B1020',
    'text-background-opacity': 0.96,
    'text-background-shape': 'roundrectangle',
    'text-background-padding': 3,
    'text-border-color': '#3EA6B8',
    'text-border-width': 1,
    'text-border-opacity': 0.7,
  },
} as const

type EdgeStyle = {
  color: string
  lineStyle: 'solid' | 'dashed' | 'dotted'
  arrow: 'triangle' | 'none'
  width?: number
  opacity?: number
  persistentLabel?: boolean
}

export const EDGE_PRESENTATION: Record<Exclude<GraphEdgeType, 'physical_fk'>, EdgeStyle> = {
  domain_membership: { color: '#E0B34D', lineStyle: 'solid', arrow: 'triangle' },
  semantic_mapping: { color: '#A78BFA', lineStyle: 'solid', arrow: 'triangle' },
  entity_mapping: { color: '#A78BFA', lineStyle: 'solid', arrow: 'triangle' },
  dimension_entity: { color: '#60A5FA', lineStyle: 'solid', arrow: 'triangle' },
  dimension_binding: { color: '#60A5FA', lineStyle: 'dotted', arrow: 'triangle' },
  metric_entity: { color: '#F2B56B', lineStyle: 'solid', arrow: 'triangle' },
  metric_dimension: { color: '#F2B56B', lineStyle: 'dashed', arrow: 'triangle' },
  metric_dependency: { color: '#F2B56B', lineStyle: 'dotted', arrow: 'triangle' },
  rule_entity: { color: '#5CCB8A', lineStyle: 'solid', arrow: 'triangle' },
  rule_dependency: { color: '#5CCB8A', lineStyle: 'dashed', arrow: 'triangle' },
  semantic_relationship: { color: '#6E7A90', lineStyle: 'solid', arrow: 'triangle', width: 1.8, persistentLabel: true },
  policy_coverage: { color: '#F17B91', lineStyle: 'dotted', arrow: 'triangle' },
  relationship_endpoint: { color: '#66728A', lineStyle: 'solid', arrow: 'none', width: 1, opacity: 0.08 },
}

/** Default (non-hovered/selected/expanded/path) edge opacity - "hidden" per spec 026, with a
 * faint residual so the canvas still reads as a connected map rather than disconnected nodes. */
export const EDGE_HIDDEN_OPACITY = 0.12

export const semanticRelationshipRules = Object.entries(EDGE_PRESENTATION).map(([type, presentation]) => ({
  selector: `edge[type = "${type}"]`,
  style: {
    width: presentation.width ?? 1.4,
    'line-color': presentation.color,
    'target-arrow-color': presentation.color,
    'target-arrow-shape': presentation.arrow,
    'arrow-scale': 1,
    'line-style': presentation.lineStyle,
    opacity: presentation.opacity ?? EDGE_HIDDEN_OPACITY,
    ...(presentation.persistentLabel ? {
      label: 'data(label)',
      color: '#C5CEDB',
      'font-family': 'JetBrains Mono',
      'font-size': 7,
      'text-background-color': '#0B1020',
      'text-background-opacity': 0.92,
      'text-background-padding': 2,
    } : {}),
  },
}))

export const ALL_EDGE_TYPES: GraphEdgeType[] = ['physical_fk', ...(Object.keys(EDGE_PRESENTATION) as GraphEdgeType[])]

export function GraphLegend() {
  const [expanded, setExpanded] = useState(true)
  const contentId = useId()

  return (
    <section className={`graph-legend ${expanded ? 'expanded' : 'collapsed'}`} aria-label="Graph edge legend">
      <button
        type="button"
        className="graph-legend-toggle"
        aria-expanded={expanded}
        aria-controls={contentId}
        aria-label={`${expanded ? 'Collapse' : 'Expand'} graph legend`}
        onClick={() => setExpanded((current) => !current)}
      >
        <span><i aria-hidden="true" />Edge legend</span>
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      <div id={contentId} className="legend" hidden={!expanded}>
        <span className="cardinality-legend"><code aria-label="Table join cardinality example: many to one">many → one</code><span>physical join</span></span>
        <span><i className="line semantic directional" /><span><code>entity → table</code> semantic mapping</span></span>
        <span><i className="line relationship directional" /><span><code>entity → entity</code> governed relationship</span></span>
        <span><i className="line dimension directional" /><span><code>dimension → entity/table</code> ownership / binding</span></span>
        <span><i className="line metric directional" /><span><code>metric → entity/dimension/table</code> ownership / compatibility / dependency</span></span>
        <span><i className="line rule directional" /><span><code>rule → semantic object</code> ownership / dependency</span></span>
        <span><i className="line policy directional" /><span><code>policy → object</code> coverage</span></span>
        <span><i className="line endpoint" /><span>relationship audit membership</span></span>
      </div>
    </section>
  )
}

export const GraphView = forwardRef<GraphHandle, GraphViewProps>(function GraphView(
  { graph, visibleIds, selectedId, usedIds, onSelect, expandedIds, focusMode, pathIds, onExpand, visibleEdgeTypes },
  ref,
) {
  const host = useRef<HTMLDivElement>(null)
  const core = useRef<Core | null>(null)
  const workerRef = useRef<Worker | null>(null)
  const [computingLayout, setComputingLayout] = useState(false)
  // Always holds the freshest visibility-recompute closure; both the visibleIds
  // effect and the zoom handler call through it so display state has one writer.
  const applyVisibilityRef = useRef<() => void>(() => {})
  // onSelect/onExpand are read through refs inside the mount effect below so tap
  // wiring doesn't force a full Cytoscape rebuild (fresh random layout) whenever
  // the caller's callback identity changes - the caller's select callback changes
  // on every selection, so depending on it made each node click re-layout the graph.
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const onExpandRef = useRef(onExpand)
  onExpandRef.current = onExpand

  useImperativeHandle(ref, () => ({
    fit: () => core.current?.fit(undefined, 52),
    resize: () => core.current?.resize(),
    reset: () => {
      core.current?.elements().removeClass('focused dimmed edge-selected path-highlighted')
      core.current?.fit(undefined, 52)
    },
  }))

  useEffect(() => {
    if (!host.current) return
    const elements: ElementDefinition[] = [
      ...graph.nodes.map((node) => ({ data: node })),
      ...graph.edges.map((edge) => ({ data: edge })),
    ]
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const cy = cytoscape({
      container: host.current,
      elements,
      minZoom: 0.35,
      maxZoom: 2.4,
      wheelSensitivity: 0.22,
      style: ([
        {
          selector: 'node',
          style: {
            'background-color': (element: cytoscape.NodeSingular) => PROFILE_PRESENTATION[element.data('profile_kind') as ProfileKind]?.color ?? PROFILE_PRESENTATION.generic.color,
            shape: (element: cytoscape.NodeSingular) => PROFILE_PRESENTATION[element.data('profile_kind') as ProfileKind]?.shape ?? PROFILE_PRESENTATION.generic.shape,
            width: (element: cytoscape.NodeSingular) => element.data('profile_kind') === 'dataset' ? 58 : element.data('profile_kind') === 'relationship' ? 22 : 34,
            height: (element: cytoscape.NodeSingular) => element.data('profile_kind') === 'dataset' ? 38 : element.data('profile_kind') === 'relationship' ? 22 : 34,
            label: 'data(label)',
            color: '#BFC8D7',
            'font-family': 'Instrument Sans',
            'font-size': 8,
            'text-valign': 'bottom',
            'text-margin-y': 7,
            'text-wrap': 'ellipsis',
            'text-max-width': 92,
            'border-width': 1,
            'border-color': '#E7ECF455',
          },
        },
        { selector: 'node[profile_kind = "relationship"]', style: { opacity: 0.66, 'font-size': 7, 'text-max-width': 74 } },
        { selector: 'edge', style: { width: 1, 'line-color': '#66728A77', 'target-arrow-color': '#66728A99', 'target-arrow-shape': 'none', 'curve-style': 'bezier', opacity: EDGE_HIDDEN_OPACITY } },
        physicalRelationshipRule,
        ...semanticRelationshipRules,
        { selector: 'node.focused', style: { 'border-width': 3, 'border-color': '#F3F6FF', 'shadow-blur': 24, 'shadow-color': '#A78BFA', 'shadow-opacity': 0.9, 'shadow-offset-x': 0, 'shadow-offset-y': 0, 'z-index': 20, opacity: 1 } },
        { selector: 'edge.focused, edge.hovered, edge.edge-selected, edge.path-highlighted', style: { width: 2.5, opacity: 1, label: 'data(label)', color: '#E7ECF4', 'font-size': 7, 'text-background-color': '#151D30', 'text-background-opacity': 0.9, 'text-background-padding': 3 } },
        { selector: 'edge[type = "physical_fk"].focused, edge[type = "physical_fk"].hovered, edge[type = "physical_fk"].edge-selected, edge[type = "physical_fk"].path-highlighted', style: { 'line-color': '#58C7D9', 'target-arrow-color': '#58C7D9', 'text-border-color': '#58C7D9', label: '' } },
        ...Object.entries(EDGE_PRESENTATION).map(([type, presentation]) => ({ selector: `edge[type = "${type}"].focused, edge[type = "${type}"].hovered, edge[type = "${type}"].edge-selected, edge[type = "${type}"].path-highlighted`, style: { 'line-color': presentation.color, 'target-arrow-color': presentation.color } })),
        { selector: 'node.path-highlighted', style: { 'border-width': 3, 'border-color': '#58C7D9', 'shadow-blur': 16, 'shadow-color': '#58C7D9', 'shadow-opacity': 0.85, 'z-index': 19, opacity: 1 } },
        { selector: '.dimmed', style: { opacity: 0.05 } },
        // Zoom-based LOD: non-focused/hovered/path labels thin out when zoomed far
        // out to cut overview clutter (relationship-node hiding is inline display,
        // applied alongside visibleIds in applyVisibilityRef so there's one writer).
        { selector: 'node.lod-label-hidden:not(.focused):not(.hovered):not(.path-highlighted)', style: { label: '' } },
      ] as any),
      layout: (graph.nodes.length > WORKER_LAYOUT_NODE_THRESHOLD
        ? { name: 'preset', padding: 52 }
        : { name: 'fcose', animate: !reducedMotion, animationDuration: reducedMotion ? 0 : 650, nodeRepulsion: 135000, idealEdgeLength: 118, gravity: 0.28, randomize: true, padding: 52, quality: 'default' }) as any,
    })
    if (graph.nodes.length > WORKER_LAYOUT_NODE_THRESHOLD) {
      setComputingLayout(true)
      const worker = new Worker(new URL('./graphLayout.worker.ts', import.meta.url), { type: 'module' })
      workerRef.current = worker
      worker.onmessage = (event: MessageEvent<LayoutResult>) => {
        cy.batch(() => {
          for (const position of event.data.positions) cy.getElementById(position.id).position({ x: position.x, y: position.y })
        })
        cy.fit(undefined, 52)
        setComputingLayout(false)
      }
      worker.postMessage({
        nodes: graph.nodes.map((node) => ({ id: node.id })),
        edges: graph.edges.map((edge) => ({ source: edge.source, target: edge.target })),
      } satisfies LayoutRequest)
    } else {
      setComputingLayout(false)
    }
    cy.on('tap', 'node', (event) => onSelectRef.current(event.target.id()))
    cy.on('dbltap', 'node', (event) => onExpandRef.current?.(event.target.id()))
    cy.on('mouseover', 'node', (event) => event.target.addClass('hovered'))
    cy.on('mouseout', 'node', (event) => event.target.removeClass('hovered'))
    cy.on('mouseover', 'edge', (event) => event.target.addClass('hovered'))
    cy.on('mouseout', 'edge', (event) => event.target.removeClass('hovered'))
    cy.on('tap', 'edge', (event) => { cy.edges().removeClass('edge-selected'); event.target.addClass('edge-selected') })
    cy.on('tap', (event) => { if (event.target === cy) cy.edges().removeClass('edge-selected') })
    cy.on('zoom', () => applyVisibilityRef.current())
    core.current = cy
    return () => {
      workerRef.current?.terminate()
      workerRef.current = null
      cy.destroy()
      core.current = null
    }
  }, [graph])

  useEffect(() => {
    applyVisibilityRef.current = () => {
      const cy = core.current
      if (!cy) return
      const zoomedFar = cy.zoom() < LOD_RELATIONSHIP_ZOOM
      const labelsThin = cy.zoom() < LOD_LABEL_ZOOM
      cy.nodes().forEach((node) => {
        const inView = visibleIds.has(node.id())
        const lodHidden = zoomedFar && node.data('profile_kind') === 'relationship'
        node.style('display', inView && !lodHidden ? 'element' : 'none')
        node.toggleClass('lod-label-hidden', labelsThin)
      })
      cy.edges().forEach((edge) => {
        const endpointsVisible = edge.source().style('display') === 'element' && edge.target().style('display') === 'element'
        const typeAllowed = !visibleEdgeTypes || visibleEdgeTypes.has(edge.data('type'))
        edge.style('display', endpointsVisible && typeAllowed ? 'element' : 'none')
      })
    }
    applyVisibilityRef.current()
    core.current?.fit(core.current.elements(':visible'), 52)
  }, [visibleIds, visibleEdgeTypes])

  useEffect(() => {
    const cy = core.current
    if (!cy) return
    // Re-establish the visibleIds/LOD baseline first, then layer focus/path
    // overrides on top - otherwise a previous isolate's forced display:none/
    // element would linger after focus mode or the selection changes.
    applyVisibilityRef.current()
    cy.elements().removeClass('focused dimmed path-highlighted')

    if (pathIds && pathIds.length > 1) {
      let pathway = cy.collection()
      for (const id of pathIds) pathway = pathway.union(cy.getElementById(id))
      for (let i = 0; i < pathIds.length - 1; i += 1) {
        pathway = pathway.union(cy.edges(
          `[source = "${pathIds[i]}"][target = "${pathIds[i + 1]}"], [source = "${pathIds[i + 1]}"][target = "${pathIds[i]}"]`,
        ))
      }
      pathway.style('display', 'element')
      cy.elements().not(pathway).addClass('dimmed')
      pathway.addClass('focused path-highlighted')
      cy.fit(pathway, 52)
      return
    }

    const expandedNeighborhood = expandedIds && expandedIds.size > 0
      ? cy.nodes().filter((node) => expandedIds.has(node.id())).closedNeighborhood()
      : cy.collection()

    if (selectedId && cy.getElementById(selectedId).length) {
      const selected = cy.getElementById(selectedId)
      const pathway = selected.closedNeighborhood().union(expandedNeighborhood)
      if (focusMode) {
        pathway.style('display', 'element')
        cy.elements().not(pathway).style('display', 'none')
        cy.fit(pathway, 52)
      } else {
        cy.elements().not(pathway).addClass('dimmed')
      }
      pathway.addClass('focused')
      selected.select()
      return
    }

    if (expandedNeighborhood.length > 0) expandedNeighborhood.addClass('focused')

    // With nothing selected, the agent's own grounding drives the focus, so an
    // answer shows which part of the knowledge it actually stood on.
    if (!usedIds || usedIds.size === 0) return
    const grounded = cy.nodes().filter((node) => usedIds.has(node.id()))
    if (grounded.length === 0) return
    const pathway = grounded.union(grounded.edgesWith(grounded))
    cy.elements().not(pathway).addClass('dimmed')
    pathway.addClass('focused')
  }, [selectedId, usedIds, expandedIds, focusMode, pathIds, visibleIds])

  return (
    <>
      <div ref={host} className="graph-host" aria-label="Semantic knowledge graph canvas" />
      {computingLayout && (
        <div className="graph-layout-progress" role="status" aria-live="polite">Computing layout…</div>
      )}
    </>
  )
})
