(({x, y}) => {
    let node = document.elementFromPoint(x, y);
    while (node && node.shadowRoot) {
        const deeper = node.shadowRoot.elementFromPoint(x, y);
        if (!deeper || deeper === node) break;
        node = deeper;
    }
    return node ? node.closest('input, textarea') : null;
})
