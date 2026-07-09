// <a2ui-surface> — recursive Lit renderer for the A2UI standard catalog (see PRD §B8).
import { LitElement, html, css, type TemplateResult, nothing } from 'lit';
import { customElement, property } from 'lit/decorators.js';
import type { BoundValue, ComponentDef } from './types.js';
import type { SurfaceState } from './processor.js';

@customElement('a2ui-surface')
export class A2UISurface extends LitElement {
  @property({ attribute: false }) surface?: SurfaceState;

  static styles = css`
    :host {
      display: block;
    }
    .a2-card {
      background: var(--card, #fff);
      border: 1px solid var(--border, #e3e6ea);
      border-radius: 12px;
      padding: 14px 16px;
      box-shadow: var(--shadow, 0 1px 3px rgba(0, 0, 0, 0.08));
    }
    .a2-col {
      display: flex;
      flex-direction: column;
    }
    .a2-row {
      display: flex;
      flex-direction: row;
    }
    .gap-small {
      gap: 6px;
    }
    .gap-medium {
      gap: 12px;
    }
    .align-center {
      align-items: center;
    }
    .align-start {
      align-items: flex-start;
    }
    .a2-divider {
      border: none;
      border-top: 1px solid var(--border, #e3e6ea);
      margin: 4px 0;
      width: 100%;
    }
    .txt-h3 {
      font-size: 1.05rem;
      font-weight: 700;
    }
    .txt-h4 {
      font-size: 0.95rem;
      font-weight: 600;
    }
    .txt-h5 {
      font-size: 0.9rem;
      font-weight: 600;
    }
    .txt-body {
      font-size: 0.9rem;
    }
    .txt-caption {
      font-size: 0.78rem;
      color: var(--fg-muted, #6b7280);
    }
    .material-symbols-outlined {
      font-family: 'Material Symbols Outlined';
      color: var(--accent, #2563eb);
    }
    button.a2-btn {
      cursor: pointer;
      border: 1px solid var(--border, #e3e6ea);
      border-radius: 8px;
      padding: 6px 12px;
      background: var(--bg-alt, #f5f6f8);
      color: var(--fg, #1a1a1a);
      font: inherit;
    }
    button.a2-btn.primary {
      background: var(--accent, #2563eb);
      color: var(--accent-fg, #fff);
      border-color: var(--accent, #2563eb);
    }
  `;

  private resolveBoundValue(
    value: BoundValue | undefined,
    ctx: string | null,
  ): string {
    if (!value) return '';
    if ('literalString' in value) return value.literalString;
    if ('literalNumber' in value) return String(value.literalNumber);
    if ('literalBoolean' in value) return String(value.literalBoolean);
    if ('path' in value) {
      let p = value.path;
      // Relative path (no leading '/') under an active context path.
      if (ctx && !p.startsWith('/')) p = `${ctx}/${p}`;
      return this.readPath(p);
    }
    return '';
  }

  private readPath(path: string): string {
    const parts = path.split('/').filter(Boolean);
    let cursor: unknown = this.surface?.dataModel ?? {};
    for (const part of parts) {
      if (cursor && typeof cursor === 'object' && part in (cursor as object)) {
        cursor = (cursor as Record<string, unknown>)[part];
      } else {
        return '';
      }
    }
    return cursor === null || cursor === undefined ? '' : String(cursor);
  }

  private renderComponent(id: string, ctx: string | null): TemplateResult | typeof nothing {
    const def: ComponentDef | undefined = this.surface?.components.get(id);
    if (!def) return nothing;
    const c = def.component;

    if ('Text' in c) {
      const hint = c.Text.usageHint ?? 'body';
      return html`<span class="txt-${hint}">${this.resolveBoundValue(c.Text.text, ctx)}</span>`;
    }
    if ('Icon' in c) {
      const name = this.resolveBoundValue(c.Icon.name, ctx);
      return html`<span class="material-symbols-outlined">${name}</span>`;
    }
    if ('Image' in c) {
      return html`<img src=${this.resolveBoundValue(c.Image.url, ctx)} alt="" />`;
    }
    if ('Divider' in c) {
      return html`<hr class="a2-divider" />`;
    }
    if ('Card' in c) {
      return html`<div class="a2-card">${this.renderComponent(c.Card.child, ctx)}</div>`;
    }
    if ('Button' in c) {
      const primary = c.Button.primary ? 'primary' : '';
      return html`<button
        class="a2-btn ${primary}"
        @click=${() =>
          this.dispatchEvent(
            new CustomEvent('a2ui-action', {
              bubbles: true,
              composed: true,
              detail: { name: c.Button.action?.name, context: ctx },
            }),
          )}
      >
        ${this.renderComponent(c.Button.child, ctx)}
      </button>`;
    }
    if ('Column' in c) {
      const cls = `a2-col gap-${c.Column.gap ?? 'small'}`;
      return html`<div class=${cls}>${this.renderChildren(c.Column.children, ctx)}</div>`;
    }
    if ('Row' in c) {
      const align = c.Row.alignment === 'start' ? 'align-start' : 'align-center';
      const cls = `a2-row ${align} gap-${c.Row.gap ?? 'small'}`;
      return html`<div class=${cls}>${this.renderChildren(c.Row.children, ctx)}</div>`;
    }
    if ('List' in c) {
      const cls = `a2-col gap-small`;
      return html`<div class=${cls}>${this.renderChildren(c.List.children, ctx)}</div>`;
    }
    return nothing;
  }

  private renderChildren(
    children:
      | { explicitList: string[] }
      | { template: { dataBinding: string; componentId: string } },
    ctx: string | null,
  ): Array<TemplateResult | typeof nothing> {
    if ('explicitList' in children) {
      return children.explicitList.map((cid) => this.renderComponent(cid, ctx));
    }
    // template: iterate over the bound array or object-map.
    const { dataBinding, componentId } = children.template;
    const value = this.resolveRaw(dataBinding);
    let items: unknown[] = [];
    if (Array.isArray(value)) items = value;
    else if (value && typeof value === 'object') items = Object.values(value);
    return items.map((_item, index) =>
      this.renderComponent(componentId, `${dataBinding}/${index}`),
    );
  }

  private resolveRaw(path: string): unknown {
    const parts = path.split('/').filter(Boolean);
    let cursor: unknown = this.surface?.dataModel ?? {};
    for (const part of parts) {
      if (cursor && typeof cursor === 'object' && part in (cursor as object)) {
        cursor = (cursor as Record<string, unknown>)[part];
      } else {
        return undefined;
      }
    }
    return cursor;
  }

  render() {
    if (!this.surface || !this.surface.ready || !this.surface.rootId) return nothing;
    return this.renderComponent(this.surface.rootId, null);
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'a2ui-surface': A2UISurface;
  }
}
