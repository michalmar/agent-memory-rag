// Recursive Lit renderer for the internal A2UI tool-card subset.
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
      max-width: 560px;
      color: var(--fg, #15223b);
      font-family: var(--font-body, system-ui, sans-serif);
    }
    *,
    *::before,
    *::after {
      box-sizing: border-box;
    }
    .a2-card {
      padding: 14px 16px;
      border: 1px solid var(--border, #d7dde6);
      border-radius: 8px;
      background: var(--card, #fff);
    }
    .a2-col {
      display: flex;
      min-width: 0;
      flex-direction: column;
    }
    .a2-row {
      display: flex;
      min-width: 0;
      flex-direction: row;
      flex-wrap: wrap;
    }
    .gap-small {
      gap: 7px;
    }
    .gap-medium {
      gap: 11px;
    }
    .align-center {
      align-items: center;
    }
    .align-start {
      align-items: flex-start;
    }
    .a2-divider {
      border: none;
      width: 100%;
      margin: 6px 0;
      border-top: 1px solid var(--border, #d7dde6);
    }
    .txt-h3,
    .txt-h4,
    .txt-h5 {
      color: var(--fg, #15223b);
      font-family: var(--font-display, system-ui, sans-serif);
      letter-spacing: -0.018em;
      line-height: 1.25;
    }
    .txt-h3 {
      font-size: 0.98rem;
      font-weight: 600;
    }
    .txt-h4 {
      font-size: 0.91rem;
      font-weight: 600;
    }
    .txt-h5 {
      font-size: 0.89rem;
      font-weight: 600;
    }
    .txt-body {
      color: var(--fg, #15223b);
      font-size: 0.88rem;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }
    .txt-caption {
      color: var(--fg-muted, #657187);
      font-family: var(--font-mono, ui-monospace, monospace);
      font-size: 0.66rem;
      line-height: 1.45;
    }
    .material-symbols-outlined {
      flex: 0 0 auto;
      color: var(--accent, #3c59c7);
      font-family: 'Material Symbols Outlined';
      font-size: 1.08rem;
    }
    @media (max-width: 560px) {
      .a2-card {
        padding: 13px 14px;
      }
      .a2-row {
        align-items: flex-start;
      }
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
    if ('Divider' in c) {
      return html`<hr class="a2-divider" />`;
    }
    if ('Card' in c) {
      return html`<div class="a2-card">${this.renderComponent(c.Card.child, ctx)}</div>`;
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
    return nothing;
  }

  private renderChildren(
    children: { explicitList: string[] },
    ctx: string | null,
  ): Array<TemplateResult | typeof nothing> {
    return children.explicitList.map((id) => this.renderComponent(id, ctx));
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
