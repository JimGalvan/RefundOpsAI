(quote) => {
  const SKIPPED_TAGS = ['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'];
  const target = quote.replace(/\s+/g, '');
  if (!target || !document.body) {
    return [];
  }

  let text = '';
  const positions = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.parentElement || SKIPPED_TAGS.includes(node.parentElement.tagName)) {
      continue;
    }
    const value = node.nodeValue;
    for (let offset = 0; offset < value.length; offset++) {
      if (!/\s/.test(value[offset])) {
        text += value[offset];
        positions.push([node, offset]);
      }
    }
  }

  function isUnobscured(element, box) {
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return hit !== null && (element.contains(hit) || hit.contains(element));
  }

  const boxes = [];
  let index = text.indexOf(target);
  while (index !== -1) {
    const [startNode, startOffset] = positions[index];
    const [endNode, endOffset] = positions[index + target.length - 1];
    const range = document.createRange();
    range.setStart(startNode, startOffset);
    range.setEnd(endNode, endOffset + 1);
    const box = range.getBoundingClientRect();
    let element = range.commonAncestorContainer;
    if (element.nodeType !== Node.ELEMENT_NODE) {
      element = element.parentElement;
    }
    const rendered = box.width > 0 && box.height > 0
      && element.checkVisibility({ opacityProperty: true, visibilityProperty: true });
    if (rendered) {
      boxes.push({
        x: box.left,
        y: box.top,
        width: box.width,
        height: box.height,
        unobscured: isUnobscured(element, box),
      });
    }
    index = text.indexOf(target, index + 1);
  }
  return boxes;
}
