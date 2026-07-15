import { LitElement } from 'lit';

export abstract class LightDomElement extends LitElement {
  protected createRenderRoot(): HTMLElement {
    return this;
  }
}
