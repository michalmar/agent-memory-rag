// Map tool-result JSON to the internal A2UI tool-card subset.
import type { A2UIMessage, ComponentDef, DataEntry } from './a2ui/types.js';
import type { CitationSource } from './client.js';
import { SHIPPING_STATUS_TEMPLATE } from './templates/shipping-status.js';

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

function citationValues(parsed: unknown): unknown[] {
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return [];
  const envelope = parsed as Record<string, unknown>;
  const data =
    envelope.data && typeof envelope.data === 'object' && !Array.isArray(envelope.data)
      ? (envelope.data as Record<string, unknown>)
      : envelope;
  const values = Array.isArray(envelope.citations) ? envelope.citations : data.citations;
  return Array.isArray(values) ? values : [];
}

export function parseCitations(value: unknown): CitationSource[] {
  const values = Array.isArray(value) ? value : citationValues(value);
  return values.flatMap((value) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
    const citation = value as Record<string, unknown>;
    if (typeof citation.ref_id !== 'string' || typeof citation.source_name !== 'string') {
      return [];
    }
    return [
      {
        ref_id: citation.ref_id,
        source_name: citation.source_name,
        search_idx:
          typeof citation.search_idx === 'number' ? citation.search_idx : undefined,
        url: typeof citation.url === 'string' ? citation.url : undefined,
      },
    ];
  });
}

export function extractToolCitations(content: string): CitationSource[] {
  try {
    return parseCitations(JSON.parse(content));
  } catch {
    return [];
  }
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

  const envelope = (parsed ?? {}) as Record<string, unknown>;
  const obj = (
    envelope.data && typeof envelope.data === 'object' && !Array.isArray(envelope.data)
      ? envelope.data
      : envelope
  ) as Record<string, unknown>;

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

  if (toolName === 'knowledge_base_retrieve' || citationValues(parsed).length > 0) return [];

  return genericDump(toolName, parsed, surfaceId);
}
