import { html, nothing, type PropertyValues } from 'lit';
import { customElement, property } from 'lit/decorators.js';

import type { ProfileDoc } from '../client.js';
import { LightDomElement } from './light-dom-element.js';

export interface ProfileDrawerActions {
  close: () => void;
  updateDraft: (value: string) => void;
  save: () => void;
  generate: () => void;
  clear: () => void;
}

@customElement('profile-drawer')
export class ProfileDrawer extends LightDomElement {
  @property({ type: Boolean }) open = false;
  @property({ attribute: false }) profile: ProfileDoc | null = null;
  @property() draft = '';
  @property({ attribute: false }) actions!: ProfileDrawerActions;

  private keydownRoot?: Document | ShadowRoot;

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.removeFocusTrap();
  }

  protected updated(changed: PropertyValues): void {
    if (!changed.has('open')) return;
    if (this.open) {
      this.installFocusTrap();
      this.querySelector<HTMLElement>('.profile-close')?.focus();
    } else {
      this.removeFocusTrap();
    }
  }

  private installFocusTrap(): void {
    this.removeFocusTrap();
    const root = this.getRootNode();
    if (root instanceof Document || root instanceof ShadowRoot) {
      this.keydownRoot = root;
      root.addEventListener('keydown', this.onRootKeydown);
    }
  }

  private removeFocusTrap(): void {
    this.keydownRoot?.removeEventListener('keydown', this.onRootKeydown);
    this.keydownRoot = undefined;
  }

  private onRootKeydown = (event: Event): void => {
    if (
      !this.open ||
      !(event instanceof KeyboardEvent) ||
      event.key !== 'Tab'
    ) return;
    const drawer = this.querySelector<HTMLElement>('.profile-drawer');
    if (!drawer) return;
    const focusable = Array.from(
      drawer.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
      ),
    );
    const first = focusable[0];
    const last = focusable.at(-1);
    if (!first || !last) return;

    const root = this.getRootNode();
    const active =
      root instanceof ShadowRoot ? root.activeElement : document.activeElement;
    if (event.shiftKey && (active === first || !drawer.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  render() {
    if (!this.open) return nothing;
    return html`
      <button
        class="drawer-scrim"
        type="button"
        aria-label="Close memory profile"
        @click=${this.actions.close}
      ></button>
      <section
        class="profile-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="profile-title"
      >
        <header class="drawer-header">
          <div class="drawer-heading">
            <p class="rail-eyebrow">Profile memory / v${this.profile?.version ?? 0}</p>
            <h2 id="profile-title">Memory profile</h2>
          </div>
          <button
            class="icon-button profile-close"
            type="button"
            aria-label="Close memory profile"
            @click=${this.actions.close}
          >
            <span class="material-symbols-outlined">close</span>
          </button>
        </header>
        <div class="drawer-body">
          <p class="field-note" id="profile-note">
            This profile stores explicit facts and preferences for the selected identity.
            Generate it from the active thread or edit the structured sections directly.
          </p>
          <label class="field-label" for="profile-editor">Profile JSON</label>
          <textarea
            id="profile-editor"
            class="profile-editor"
            aria-describedby="profile-note"
            spellcheck="false"
            .value=${this.draft}
            @input=${(event: Event) =>
              this.actions.updateDraft(
                (event.target as HTMLTextAreaElement).value,
              )}
          ></textarea>
        </div>
        <div class="drawer-actions">
          <button class="primary-button" type="button" @click=${this.actions.save}>
            Save changes
          </button>
          <button
            class="secondary-button"
            type="button"
            title="Extract profile facts from the active thread"
            @click=${this.actions.generate}
          >
            Generate from thread
          </button>
          <button class="danger-button" type="button" @click=${this.actions.clear}>
            Clear profile
          </button>
        </div>
      </section>
    `;
  }
}
