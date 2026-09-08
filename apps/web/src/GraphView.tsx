import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import { ChevronDown } from 'lucide-react'
import { forwardRef, useEffect, useId, useImperativeHandle, useRef, useState } from 'react'
import { PROFILE_PRESENTATION } from './profilePresentation'
import type { GraphEdgeType, GraphResponse, ProfileKind } from './types'

export interface GraphHandle {
  fit: () => void
  resize: () => void
  reset: () => void
}

interface GraphViewProps {
  graph: GraphResponse
  visibleIds: Set<string>
  selectedId: string | null
  onSelect: (id: string) => void
}

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
  relationship_endpoint: { color: '#66728A', lineStyle: 'solid', arrow: 'none', width: 1, opacity: 0.42 },
}

export const semanticRelationshipRules = Object.entries(EDGE_PRESENTATION).map(([type, presentation]) => ({
  selector: `edge[type = "${type}"]`,
  style: {
    width: presentation.width ?? 1.4,
    'line-color': presentation.color,
    'target-arrow-color': presentation.color,
    'target-arrow-shape': presentation.arrow,
    'arrow-scale': 1,
    'line-style': presentation.lineStyle,
    opacity: presentation.opacity ?? 0.82,
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
  { graph, visibleIds, selectedId, onSelect },
  ref,
) {
  const host = useRef<HTMLDivElement>(null)
  const core = useRef<Core | null>(null)

  useImperativeHandle(ref, () => ({
    fit: () => core.current?.fit(undefined, 52),
    resize: () => core.current?.resize(),
    reset: () => {
      core.current?.elements().removeClass('focused dimmed edge-selected')
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
        { selector: 'edge', style: { width: 1, 'line-color': '#66728A77', 'target-arrow-color': '#66728A99', 'target-arrow-shape': 'none', 'curve-style': 'bezier', opacity: 0.82 } },
        physicalRelationshipRule,
        ...semanticRelationshipRules,
        { selector: 'node.focused', style: { 'border-width': 3, 'border-color': '#F3F6FF', 'shadow-blur': 24, 'shadow-color': '#A78BFA', 'shadow-opacity': 0.9, 'shadow-offset-x': 0, 'shadow-offset-y': 0, 'z-index': 20, opacity: 1 } },
        { selector: 'edge.focused, edge.hovered, edge.edge-selected', style: { width: 2.5, opacity: 1, label: 'data(label)', color: '#E7ECF4', 'font-size': 7, 'text-background-color': '#151D30', 'text-background-opacity': 0.9, 'text-background-padding': 3 } },
        { selector: 'edge[type = "physical_fk"].focused, edge[type = "physical_fk"].hovered, edge[type = "physical_fk"].edge-selected', style: { 'line-color': '#58C7D9', 'target-arrow-color': '#58C7D9', 'text-border-color': '#58C7D9', label: '' } },
        ...Object.entries(EDGE_PRESENTATION).map(([type, presentation]) => ({ selector: `edge[type = "${type}"].focused, edge[type = "${type}"].hovered, edge[type = "${type}"].edge-selected`, style: { 'line-color': presentation.color, 'target-arrow-color': presentation.color } })),
        { selector: '.dimmed', style: { opacity: 0.1 } },
      ] as any),
      layout: { name: 'cose', animate: !reducedMotion, animationDuration: reducedMotion ? 0 : 650, nodeRepulsion: () => 135000, idealEdgeLength: () => 118, edgeElasticity: () => 90, gravity: 0.28, randomize: true, padding: 52 },
    })
    cy.on('tap', 'node', (event) => onSelect(event.target.id()))
    cy.on('mouseover', 'node', (event) => event.target.addClass('hovered'))
    cy.on('mouseout', 'node', (event) => event.target.removeClass('hovered'))
    cy.on('mouseover', 'edge', (event) => event.target.addClass('hovered'))
    cy.on('mouseout', 'edge', (event) => event.target.removeClass('hovered'))
    cy.on('tap', 'edge', (event) => { cy.edges().removeClass('edge-selected'); event.target.addClass('edge-selected') })
    cy.on('tap', (event) => { if (event.target === cy) cy.edges().removeClass('edge-selected') })
    core.current = cy
    return () => { cy.destroy(); core.current = null }
  }, [graph, onSelect])

  useEffect(() => {
    const cy = core.current
    if (!cy) return
    cy.nodes().forEach((node) => { node.style('display', visibleIds.has(node.id()) ? 'element' : 'none') })
    cy.edges().forEach((edge) => { edge.style('display', visibleIds.has(edge.source().id()) && visibleIds.has(edge.target().id()) ? 'element' : 'none') })
    cy.fit(cy.elements(':visible'), 52)
  }, [visibleIds])

  useEffect(() => {
    const cy = core.current
    if (!cy) return
    cy.elements().removeClass('focused dimmed')
    if (!selectedId || !cy.getElementById(selectedId).length) return
    const selected = cy.getElementById(selectedId)
    const pathway = selected.closedNeighborhood()
    cy.elements().not(pathway).addClass('dimmed')
    pathway.addClass('focused')
    selected.select()
  }, [selectedId])

  return <div ref={host} className="graph-host" aria-label="Semantic knowledge graph canvas" />
})
