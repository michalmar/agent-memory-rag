// converters.ts — map tool-result JSON to A2UI messages (see PRD §B8).
import type { A2UIMessage, ComponentDef, DataEntry } from './a2ui/types.js';
import { SHIPPING_STATUS_TEMPLATE } from './templates/shipping-status.js';
import { RAG_CITATIONS_TEMPLATE } from './templates/rag-citations.js';

function flatten(model: Record<string, unknown>): DataEntry[] {
  const entries: DataEntry[] = [];
  for (const [key, value] of Object.entries(model)) {
    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      entries.push({ key, valueMap: flatten(value as Record<string, unknown>) });
    } else if (typeof value === 'number') {
      entries.push({ key, valueNumber: value });
    } else if (typeof value === 'boolean') {
      entries.push({ key, valueBoolean: value });
    } else {
      entries.push({ key, valueString: value == null ? '' : String(value) });
    }
  }
  return entries;
}

function inflateSurfaceTemplate(
  template: ComponentDef[],
  dataModel: Record<string, unknown>,
  surfaceId: string,
): A2UIMessage[] {
  return [
    { surfaceUpdate: { surfaceId, components: template } },
    { dataModelUpdate: { surfaceId, contents: flatten(dataModel) } },
    { beginRendering: { surfaceId, root: 'root' } },
  ];
}

interface Citation {
  search_idx: number;
  ref_id: string;
  source_name: string;
  content: string;
  annotation: string;
}

function buildCitationModel(citations: Citation[]): Record<string, unknown> {
  const citationMap: Record<string, unknown> = {};
  citations.forEach((c, i) => {
    citationMap[String(i)] = {
      sourceName: `${c.annotation}  ${c.source_name}`,
      snippet: (c.content ?? '').slice(0, 150),
    };
  });
  return {
    citationCount: `${citations.length} sources`,
    citations: citationMap,
  };
}

function genericDump(toolName: string, parsed: unknown, surfaceId: string): A2UIMessage[] {
  const template: ComponentDef[] = [
    { id: 'root', component: { Card: { child: 'dump' } } },
    { id: 'dump', component: { Text: { text: { path: '/json' }, usageHint: 'caption' } } },
  ];
  return inflateSurfaceTemplate(
    template,
    { json: `${toolName}: ${JSON.stringify(parsed, null, 2)}` },
    surfaceId,
  );
}

/**
 * Convert a tool-result JSON string into A2UI messages for the given surface.
 * Returns [] when there is no surface to render (e.g. RAG with no citations).
 */
export function convertToolResult(
  toolName: string,
  content: string,
  surfaceId: string,
): A2UIMessage[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    return [];
  }

  const obj = (parsed ?? {}) as Record<string, unknown>;

  if (toolName === 'get_order_status') {
    if (obj.status === 'not_found') return [];
    return inflateSurfaceTemplate(
      SHIPPING_STATUS_TEMPLATE,
      {
        trackingNumber: obj.trackingNumber ?? '',
        currentStepIcon: obj.currentStepIcon ?? 'help',
        eta: obj.eta ?? '',
      },
      surfaceId,
    );
  }

  // Classic RAG or any result carrying a citations array (agentic MCP).
  if (toolName === 'do_classic_rag' || Array.isArray(obj.citations)) {
    const citations = (obj.citations as Citation[]) ?? [];
    if (citations.length === 0) return [];
    return inflateSurfaceTemplate(
      RAG_CITATIONS_TEMPLATE,
      buildCitationModel(citations),
      surfaceId,
    );
  }

  return genericDump(toolName, parsed, surfaceId);
}
