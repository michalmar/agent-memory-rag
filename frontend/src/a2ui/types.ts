// Internal A2UI subset used by generated application-tool cards.

export type BoundValue =
  | { literalString: string }
  | { literalNumber: number }
  | { literalBoolean: boolean }
  | { path: string };

export interface Children {
  explicitList: string[];
}

export interface TextComponent {
  Text: { text: BoundValue; usageHint?: string };
}
export interface IconComponent {
  Icon: { name: BoundValue };
}
export interface CardComponent {
  Card: { child: string };
}
export interface ColumnComponent {
  Column: { children: Children; gap?: string };
}
export interface RowComponent {
  Row: { children: Children; alignment?: string; distribution?: string; gap?: string };
}
export interface DividerComponent {
  Divider: Record<string, never>;
}

export type Component =
  | TextComponent
  | IconComponent
  | CardComponent
  | ColumnComponent
  | RowComponent
  | DividerComponent;

export interface ComponentDef {
  id: string;
  component: Component;
}

// ---- Server → client messages ----
export interface DataEntry {
  key: string;
  valueString?: string;
  valueNumber?: number;
  valueBoolean?: boolean;
  valueMap?: DataEntry[];
}

export interface SurfaceUpdateMessage {
  surfaceUpdate: { surfaceId: string; components: ComponentDef[] };
}
export interface DataModelUpdateMessage {
  dataModelUpdate: { surfaceId: string; contents: DataEntry[] };
}
export interface BeginRenderingMessage {
  beginRendering: { surfaceId: string; root: string };
}
export type A2UIMessage =
  | SurfaceUpdateMessage
  | DataModelUpdateMessage
  | BeginRenderingMessage;
