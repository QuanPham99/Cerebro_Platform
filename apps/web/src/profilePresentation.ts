import type cytoscape from 'cytoscape'
import type { ProfileKind } from './types'

export type ProfileLayer = 'Physical' | 'Semantic' | 'Metrics' | 'Governance' | 'Other'
export type LayerPreset = 'All' | Exclude<ProfileLayer, 'Other'>

export interface ProfilePresentation {
  kind: ProfileKind
  label: string
  plural: string
  layer: ProfileLayer
  color: string
  shape: cytoscape.Css.NodeShape
}

export const PROFILE_PRESENTATION: Record<ProfileKind, ProfilePresentation> = {
  dataset: { kind: 'dataset', label: 'Dataset', plural: 'Datasets', layer: 'Physical', color: '#58C7D9', shape: 'round-rectangle' },
  physical_table: { kind: 'physical_table', label: 'Physical table', plural: 'Physical tables', layer: 'Physical', color: '#3EA6B8', shape: 'rectangle' },
  entity: { kind: 'entity', label: 'Entity', plural: 'Entities', layer: 'Semantic', color: '#A78BFA', shape: 'ellipse' },
  dimension: { kind: 'dimension', label: 'Dimension', plural: 'Dimensions', layer: 'Semantic', color: '#60A5FA', shape: 'barrel' },
  business_rule: { kind: 'business_rule', label: 'Business rule', plural: 'Business rules', layer: 'Semantic', color: '#5CCB8A', shape: 'octagon' },
  metric: { kind: 'metric', label: 'Metric', plural: 'Metrics', layer: 'Metrics', color: '#F2B56B', shape: 'hexagon' },
  relationship: { kind: 'relationship', label: 'Relationship', plural: 'Relationships', layer: 'Semantic', color: '#6E7A90', shape: 'diamond' },
  policy: { kind: 'policy', label: 'Policy', plural: 'Policies', layer: 'Governance', color: '#F17B91', shape: 'tag' },
  legacy_concept: { kind: 'legacy_concept', label: 'Legacy concept', plural: 'Legacy concepts', layer: 'Semantic', color: '#8B79C6', shape: 'ellipse' },
  generic: { kind: 'generic', label: 'Generic', plural: 'Other objects', layer: 'Other', color: '#8993A8', shape: 'ellipse' },
}

export const PROFILE_KINDS = Object.keys(PROFILE_PRESENTATION) as ProfileKind[]
export const LAYER_PRESETS: LayerPreset[] = ['All', 'Physical', 'Semantic', 'Metrics', 'Governance']

export function kindsForLayer(layer: LayerPreset): ProfileKind[] {
  if (layer === 'All') return PROFILE_KINDS
  return PROFILE_KINDS.filter((kind) => PROFILE_PRESENTATION[kind].layer === layer)
}
