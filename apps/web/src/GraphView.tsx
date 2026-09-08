import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import type { GraphResponse } from './types'

export interface GraphHandle {
  fit: () => void
  reset: () => void
}

interface GraphViewProps {
  graph: GraphResponse
  visibleIds: Set<string>
  selectedId: string | null
  /** Objects the agent actually grounded its last answer in. */
  usedIds?: Set<string>
  onSelect: (id: string) => void
}

const colors: Record<string, string> = {
  dataset: '#58C7D9',
  table: '#3EA6B8',
  concept: '#A78BFA',
  relationship: '#6E7A90',
  metric: '#F2B56B',
  policy: '#F17B91',
}

const shapes: Record<string, cytoscape.Css.NodeShape> = {
  dataset: 'round-rectangle',
  table: 'rectangle',
  concept: 'ellipse',
  relationship: 'diamond',
  metric: 'hexagon',
  policy: 'tag',
}

function cardinalityEndpoint(edge: cytoscape.EdgeSingular, endpoint: 0 | 1) {
  const value = String(edge.data('label') ?? '').split('-to-')[endpoint]
  return value === 'one' || value === 'many' ? value : ''
}

export const physicalRelationshipRule = {
  selector: 'edge[type = "physical_fk"]',
  style: {
    width: 1.4,
    'line-color': '#3EA6B8AA',
    'target-arrow-color': '#3EA6B8',
    'target-arrow-shape': 'triangle',
    'arrow-scale': 0.72,
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

export function GraphLegend() {
  return (
    <div className="legend" aria-label="Graph edge legend">
      <span className="cardinality-legend">
        <code aria-label="Table join cardinality example: many to one">many → one</code>
        <span>table join</span>
      </span>
      <span><i className="line semantic" /> semantic path</span>
      <span><i className="line policy" /> governance</span>
    </div>
  )
}

export const GraphView = forwardRef<GraphHandle, GraphViewProps>(function GraphView(
  { graph, visibleIds, selectedId, usedIds, onSelect },
  ref,
) {
  const host = useRef<HTMLDivElement>(null)
  const core = useRef<Core | null>(null)

  useImperativeHandle(ref, () => ({
    fit: () => core.current?.fit(undefined, 52),
    reset: () => {
      core.current?.elements().removeClass('focused dimmed')
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
            'background-color': (element: cytoscape.NodeSingular) => colors[element.data('type')] || '#8491A7',
            shape: (element: cytoscape.NodeSingular) => shapes[element.data('type')] || 'ellipse',
            width: (element: cytoscape.NodeSingular) => (element.data('type') === 'dataset' ? 58 : 34),
            height: (element: cytoscape.NodeSingular) => (element.data('type') === 'dataset' ? 38 : 34),
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
        {
          selector: 'edge',
          style: {
            width: 1,
            'line-color': '#66728A77',
            'target-arrow-color': '#66728A99',
            'target-arrow-shape': (element: cytoscape.EdgeSingular) => (element.data('type') === 'semantic_mapping' ? 'triangle' : 'none'),
            'curve-style': 'bezier',
            opacity: 0.62,
          },
        },
        physicalRelationshipRule,
        {
          selector: 'node.focused',
          style: {
            'border-width': 3,
            'border-color': '#F3F6FF',
            'shadow-blur': 24,
            'shadow-color': '#A78BFA',
            'shadow-opacity': 0.9,
            'shadow-offset-x': 0,
            'shadow-offset-y': 0,
            'z-index': 20,
          },
        },
        {
          selector: 'edge.focused',
          style: {
            width: 2.5,
            'line-color': '#A78BFA',
            'target-arrow-color': '#A78BFA',
            opacity: 1,
            label: 'data(label)',
            color: '#E7ECF4',
            'font-size': 7,
            'text-background-color': '#151D30',
            'text-background-opacity': 0.9,
            'text-background-padding': 3,
          },
        },
        {
          selector: 'edge[type = "physical_fk"].focused',
          style: {
            'line-color': '#58C7D9',
            'target-arrow-color': '#58C7D9',
            'text-border-color': '#58C7D9',
            label: '',
          },
        },
        { selector: '.dimmed', style: { opacity: 0.1 } },
      ] as any),
      layout: {
        name: 'cose',
        animate: !reducedMotion,
        animationDuration: reducedMotion ? 0 : 650,
        nodeRepulsion: () => 135000,
        idealEdgeLength: () => 118,
        edgeElasticity: () => 90,
        gravity: 0.28,
        randomize: true,
        padding: 52,
      },
    })
    cy.on('tap', 'node', (event) => onSelect(event.target.id()))
    cy.on('mouseover', 'node', (event) => event.target.addClass('hovered'))
    cy.on('mouseout', 'node', (event) => event.target.removeClass('hovered'))
    core.current = cy
    return () => {
      cy.destroy()
      core.current = null
    }
  }, [graph, onSelect])

  useEffect(() => {
    const cy = core.current
    if (!cy) return
    cy.nodes().forEach((node) => {
      node.style('display', visibleIds.has(node.id()) ? 'element' : 'none')
    })
    cy.edges().forEach((edge) => {
      edge.style('display', visibleIds.has(edge.source().id()) && visibleIds.has(edge.target().id()) ? 'element' : 'none')
    })
    cy.fit(cy.elements(':visible'), 52)
  }, [visibleIds])

  useEffect(() => {
    const cy = core.current
    if (!cy) return
    cy.elements().removeClass('focused dimmed')
    if (selectedId && cy.getElementById(selectedId).length) {
      const selected = cy.getElementById(selectedId)
      const pathway = selected.closedNeighborhood()
      cy.elements().not(pathway).addClass('dimmed')
      pathway.addClass('focused')
      selected.select()
      return
    }
    // With nothing selected, the agent's own grounding drives the focus, so an
    // answer shows which part of the knowledge it actually stood on.
    if (!usedIds || usedIds.size === 0) return
    const grounded = cy.nodes().filter((node) => usedIds.has(node.id()))
    if (grounded.length === 0) return
    const pathway = grounded.union(grounded.edgesWith(grounded))
    cy.elements().not(pathway).addClass('dimmed')
    pathway.addClass('focused')
  }, [selectedId, usedIds])

  return <div ref={host} className="graph-host" aria-label="Semantic knowledge graph canvas" />
})
