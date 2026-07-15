import { html, nothing } from 'lit';
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
