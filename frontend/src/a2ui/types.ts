// A2UI v0.8 — minimal "standard catalog" message & component types (see PRD §B8).

export type BoundValue =
  | { literalString: string }
  | { literalNumber: number }
  | { literalBoolean: boolean }
  | { path: string };

export type Children =
  | { explicitList: string[] }
  | { template: { dataBinding: string; componentId: string } };

export interface TextComponent {
  Text: { text: BoundValue; usageHint?: string };
}
export interface IconComponent {
  Icon: { name: BoundValue };
}
export interface ImageComponent {
  Image: { url: BoundValue };
}
export interface ButtonComponent {
  Button: { child: string; primary?: boolean; action?: { name: string } };
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
export interface ListComponent {
  List: { children: Children; direction?: string };
}
export interface DividerComponent {
  Divider: Record<string, never>;
}

export type Component =
  | TextComponent
  | IconComponent
  | ImageComponent
  | ButtonComponent
  | CardComponent
  | ColumnComponent
  | RowComponent
  | ListComponent
  | DividerComponent;

export interface ComponentDef {
  id: string;
  weight?: number;
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
  dataModelUpdate: { surfaceId: string; path?: string; contents: DataEntry[] };
}
export interface BeginRenderingMessage {
  beginRendering: { surfaceId: string; root: string };
}
export interface DeleteSurfaceMessage {
  deleteSurface: { surfaceId: string };
}

export type A2UIMessage =
  | SurfaceUpdateMessage
  | DataModelUpdateMessage
  | BeginRenderingMessage
  | DeleteSurfaceMessage;
