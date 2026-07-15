// Buffers the internal A2UI subset emitted by tool-result converters.
import type {
  A2UIMessage,
  ComponentDef,
  DataEntry,
} from './types.js';

export interface SurfaceState {
  surfaceId: string;
  components: Map<string, ComponentDef>;
  dataModel: Record<string, unknown>;
  rootId: string | null;
  ready: boolean;
}

function entriesToObject(entries: DataEntry[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const e of entries) {
    if (e.valueMap !== undefined) out[e.key] = entriesToObject(e.valueMap);
    else if (e.valueString !== undefined) out[e.key] = e.valueString;
    else if (e.valueNumber !== undefined) out[e.key] = e.valueNumber;
    else if (e.valueBoolean !== undefined) out[e.key] = e.valueBoolean;
    else out[e.key] = null;
  }
  return out;
}

export class A2UIProcessor {
  private surfaces = new Map<string, SurfaceState>();

  getSurface(surfaceId: string): SurfaceState | undefined {
    return this.surfaces.get(surfaceId);
  }

  private ensure(surfaceId: string): SurfaceState {
    let s = this.surfaces.get(surfaceId);
    if (!s) {
      s = {
        surfaceId,
        components: new Map(),
        dataModel: {},
        rootId: null,
        ready: false,
      };
      this.surfaces.set(surfaceId, s);
    }
    return s;
  }

  apply(msg: A2UIMessage): void {
    if ('surfaceUpdate' in msg) {
      const s = this.ensure(msg.surfaceUpdate.surfaceId);
      for (const c of msg.surfaceUpdate.components) s.components.set(c.id, c);
    } else if ('dataModelUpdate' in msg) {
      const { surfaceId, contents } = msg.dataModelUpdate;
      const s = this.ensure(surfaceId);
      s.dataModel = { ...s.dataModel, ...entriesToObject(contents) };
    } else if ('beginRendering' in msg) {
      const s = this.ensure(msg.beginRendering.surfaceId);
      s.rootId = msg.beginRendering.root;
      s.ready = true;
    }
  }
}
