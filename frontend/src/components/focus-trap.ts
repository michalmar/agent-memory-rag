const FOCUSABLE_SELECTOR = [
  'a[href]:not([tabindex="-1"])',
  'button:not([disabled]):not([tabindex="-1"])',
  'input:not([disabled]):not([tabindex="-1"])',
  'select:not([disabled]):not([tabindex="-1"])',
  'textarea:not([disabled]):not([tabindex="-1"])',
  'iframe:not([tabindex="-1"])',
  '[tabindex]:not([tabindex="-1"]):not([data-focus-guard])',
].join(', ');

export function focusableElements(
  container: HTMLElement,
): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter(
    (element) =>
      !element.hasAttribute('hidden')
      && !element.closest('[hidden]'),
  );
}

export function trapFocusWithin(
  event: KeyboardEvent,
  container: HTMLElement,
): void {
  if (event.key !== 'Tab') return;
  const focusable = focusableElements(container);
  const first = focusable[0];
  const last = focusable.at(-1);
  if (!first || !last) return;

  const root = container.getRootNode();
  const active =
    root instanceof ShadowRoot ? root.activeElement : document.activeElement;
  if (event.shiftKey && (active === first || !container.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}
