import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Box, Filter, Focus, MessageSquare, Network, RefreshCw, Search, Sparkles, X } from 'lucide-react'
import { getBundle, getConcept, getGraph } from './api'
import { ChatPanel, usedObjectIds } from './ChatPanel'
import { getAgentStatus } from './chatApi'
import { GraphLegend, GraphView, type GraphHandle } from './GraphView'
import { Inspector } from './Inspector'
import { NodeNavigator, nodeTypes } from './NodeNavigator'
import type { AgentResponse, BundleInfo, GraphResponse, NodeType, SemanticObject } from './types'

export default function App() {
  const [graph, setGraph] = useState<GraphResponse | null>(null)
  const [bundle, setBundle] = useState<BundleInfo | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [types, setTypes] = useState<Set<NodeType>>(new Set(nodeTypes))
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<SemanticObject | null>(null)
  const [inspectorLoading, setInspectorLoading] = useState(false)
  const [agentReady, setAgentReady] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const [usedIds, setUsedIds] = useState<Set<string>>(new Set())
  const graphHandle = useRef<GraphHandle>(null)

  const load = useCallback(() => {
    const controller = new AbortController()
    setError('')
    Promise.all([getGraph(controller.signal), getBundle(controller.signal)])
      .then(([nextGraph, nextBundle]) => { setGraph(nextGraph); setBundle(nextBundle) })
      .catch((reason: Error) => { if (reason.name !== 'AbortError') setError(reason.message) })
    return () => controller.abort()
  }, [])

  useEffect(load, [load])
  useEffect(() => {
    const controller = new AbortController()
    getAgentStatus(controller.signal)
      .then((status) => setAgentReady(status === 'ready'))
      .catch(() => setAgentReady(false))
    return () => controller.abort()
  }, [])
  // An answer's grounding drives the graph focus, so selecting a node by hand
  // must take precedence: the two focus sources would otherwise fight.
  const onTurn = useCallback((response: AgentResponse | null) => {
    setSelectedId(null)
    setSelected(null)
    setUsedIds(usedObjectIds(response))
  }, [])

  const select = useCallback((id: string) => {
    setUsedIds(new Set())
    setSelectedId(id)
    setInspectorLoading(true)
    getConcept(id).then(setSelected).catch((reason: Error) => setError(reason.message)).finally(() => setInspectorLoading(false))
  }, [])

  const visibleIds = useMemo(() => {
    if (!graph) return new Set<string>()
    const needle = query.trim().toLowerCase()
    return new Set(graph.nodes.filter((node) => types.has(node.type) && (!needle || `${node.label} ${node.id} ${node.description}`.toLowerCase().includes(needle))).map((node) => node.id))
  }, [graph, query, types])

  const toggleType = (type: NodeType) => setTypes((current) => {
    const next = new Set(current)
    if (next.has(type)) next.delete(type); else next.add(type)
    return next
  })

  const reset = () => {
    setQuery('')
    setTypes(new Set(nodeTypes))
    setSelectedId(null)
    setSelected(null)
    setUsedIds(new Set())
    graphHandle.current?.reset()
  }

  if (error && !graph) {
    return <main className="state-page"><Network size={44} /><h1>Constellation unavailable</h1><p>{error}</p><button onClick={load}><RefreshCw size={16} /> Try again</button></main>
  }
  if (!graph) return <main className="state-page loading"><Sparkles size={40} /><h1>Mapping semantic space…</h1><p>Loading the reviewed OKF bundle.</p></main>

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><div className="brand-mark"><Network size={19} /></div><div><strong>Cerebro</strong><span>Semantic constellation</span></div></div>
        <div className="bundle-chip"><i />{bundle?.name}<code>v{bundle?.version}</code></div>
        <div className="top-actions"><span>{visibleIds.size} / {graph.nodes.length} objects</span>{agentReady && <button className={chatOpen ? 'active' : ''} title="Ask the data" onClick={() => setChatOpen((open) => !open)}><MessageSquare size={17} /></button>}<button title="Fit graph" onClick={() => graphHandle.current?.fit()}><Focus size={17} /></button><button title="Reset view" onClick={reset}><RefreshCw size={17} /></button></div>
      </header>

      <aside className="discovery-rail">
        <div className="rail-heading"><span>Discover</span><kbd>⌘ K</kbd></div>
        <label className="search-box"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search meaning, table, metric…" aria-label="Search semantic objects" />{query && <button onClick={() => setQuery('')} aria-label="Clear search"><X size={14} /></button>}</label>
        <div className="filter-title"><Filter size={13} /> Object layers</div>
        <div className="type-filters">
          {nodeTypes.map((type) => <label key={type}><input type="checkbox" checked={types.has(type)} onChange={() => toggleType(type)} /><span className={`type-symbol ${type}`} /><span>{type}</span><em>{graph.nodes.filter((node) => node.type === type).length}</em></label>)}
        </div>
        <div className="navigator-heading"><Box size={13} /> Keyboard navigator</div>
        <NodeNavigator nodes={graph.nodes} visibleIds={visibleIds} selectedId={selectedId} onSelect={select} />
        <GraphLegend />
      </aside>

      <section className="canvas-wrap">
        <div className="canvas-label"><span>Bank workshop</span><small>Drag to pan · Scroll to zoom · Select to trace</small></div>
        <GraphView ref={graphHandle} graph={graph} visibleIds={visibleIds} selectedId={selectedId} usedIds={usedIds} onSelect={select} />
        {!chatOpen && <div className="layer-caption"><span>Physical structures</span><i /><span>Business meaning</span><i /><span>Governed metrics</span></div>}
        {chatOpen && <ChatPanel onClose={() => setChatOpen(false)} onTurn={onTurn} />}
      </section>

      <Inspector object={selected} loading={inspectorLoading} />
      {error && <div className="toast">{error}<button onClick={() => setError('')}><X size={14} /></button></div>}
    </main>
  )
}
