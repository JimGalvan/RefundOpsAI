((sensitiveControls) => {
  const SKIPPED_TYPES = ['password', 'hidden', 'submit', 'button', 'reset', 'image', 'file'];

  function isVisible(control) {
    const style = window.getComputedStyle(control);
    if (style.display === 'none' || style.visibility === 'hidden') {
      return false;
    }
    const box = control.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  }

  function labelOf(control) {
    const ariaLabel = control.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) {
      return ariaLabel.trim();
    }
    if (control.labels && control.labels.length > 0 && control.labels[0].innerText.trim()) {
      return control.labels[0].innerText.trim();
    }
    const placeholder = control.getAttribute('placeholder');
    if (placeholder && placeholder.trim()) {
      return placeholder.trim();
    }
    return control.getAttribute('name') || control.id || control.tagName.toLowerCase();
  }

  function valueOf(control, type) {
    if (control.tagName === 'SELECT') {
      const option = control.options[control.selectedIndex];
      return option ? option.text.trim() : '';
    }
    if (type === 'checkbox' || type === 'radio') {
      return control.checked ? 'checked' : 'unchecked';
    }
    return control.value;
  }

  const sensitive = new Set(sensitiveControls || []);
  const fields = [];
  for (const control of document.querySelectorAll('input, textarea, select')) {
    const type = (control.getAttribute('type') || '').toLowerCase();
    if (sensitive.has(control) || SKIPPED_TYPES.includes(type) || !isVisible(control)) {
      continue;
    }
    const box = control.getBoundingClientRect();
    fields.push({
      label: labelOf(control),
      value: valueOf(control, type),
      box: { x: box.left, y: box.top, width: box.width, height: box.height },
    });
  }
  return fields;
})
