/* AG Grid views for the console: the case queue and the evidence ledger.
   Progressive enhancement: the server-rendered table and evidence cards stay in the page and keep working
   if the grid library cannot load; the grids take over only when it has. */
(function () {
  "use strict";

  function gridTheme() {
    return agGrid.themeQuartz.withParams({
      accentColor: "#1b45cf",
      backgroundColor: "#ffffff",
      foregroundColor: "#101b33",
      borderColor: "#d5dbe7",
      headerBackgroundColor: "#fafbfe",
      headerTextColor: "#5d6882",
      headerFontSize: 11,
      headerFontWeight: 600,
      fontFamily: '"Public Sans", system-ui, sans-serif',
      fontSize: 14,
      rowHoverColor: "#f4f7fd",
      wrapperBorderRadius: 4,
      browserColorScheme: "light",
    });
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  }

  function readRows(id) {
    const holder = document.getElementById(id);
    return holder ? JSON.parse(holder.textContent) : null;
  }

  const cells = {
    caseCell(params) {
      const box = node("div", "grid-case");
      const link = node("a", "case-link", params.data.order_ref || params.data.dispute_id);
      link.href = params.data.href;
      box.append(link, node("div", "muted", params.data.buyer + (params.data.offline ? " · offline demo" : "")));
      return box;
    },
    stateCell(params) {
      return node("span", "chip chip-" + params.data.state_tone, params.data.state_label);
    },
    dueCell(params) {
      const box = node("div");
      if (!params.data.due_date) return box;
      box.append(
        node("span", params.data.due_urgent ? "due-urgent" : "", params.data.due_text),
        node("span", "faint", " · " + params.data.due_date)
      );
      return box;
    },
    sourceCell(params) {
      return node("span", "ledger-tab tint-" + params.data.key, params.data.source);
    },
    supportsCell(params) {
      return node("span", params.data.supports === "untrusted" ? "supports-untrusted" : "", params.data.supports_label);
    },
    quoteCell(params) {
      return node("span", "mono grid-quote", params.value);
    },
  };

  let queueApi = null;
  let ledgerApi = null;

  function initQueue() {
    const container = document.getElementById("queue-grid");
    const table = document.getElementById("queue-table");
    const rows = readRows("queue-data");
    if (!container || !table || !rows || !window.agGrid || container.dataset.ready) return;
    if (!window.matchMedia("(min-width: 721px)").matches) return; // phones keep the stacked table
    container.dataset.ready = "1";
    if (queueApi) queueApi.destroy(); // it belonged to a queue that htmx has since replaced
    queueApi = agGrid.createGrid(container, {
      theme: gridTheme(),
      rowData: rows,
      getRowId: (params) => String(params.data.id),
      domLayout: "autoHeight",
      animateRows: false,
      rowHeight: 64,
      defaultColDef: { sortable: true, filter: true, resizable: true },
      columnDefs: [
        { headerName: "Case", field: "order_ref", flex: 2, minWidth: 200, cellRenderer: cells.caseCell,
          getQuickFilterText: (params) => [params.data.order_ref, params.data.dispute_id, params.data.buyer].join(" "),
          filterValueGetter: (params) => params.data.order_ref + " " + params.data.buyer },
        { headerName: "Reason", field: "reason", flex: 1.4, minWidth: 150 },
        { headerName: "Amount", field: "amount", type: "numericColumn", filter: "agNumberColumnFilter", flex: 1, minWidth: 120,
          valueFormatter: (params) => params.value.toFixed(2) + " " + params.data.currency, cellClass: "mono" },
        { headerName: "Respond by", field: "due_sort", flex: 1.3, minWidth: 160, sort: "asc", filter: false, cellRenderer: cells.dueCell },
        { headerName: "Status", field: "state_label", flex: 1.3, minWidth: 170, cellRenderer: cells.stateCell },
      ],
      onRowClicked: (event) => {
        if (!event.event.target.closest("a")) window.location.href = event.data.href;
      },
    });
    table.hidden = true;
    container.hidden = false;
    const tools = document.getElementById("queue-tools");
    if (tools) tools.hidden = false;
    const filter = document.getElementById("queue-filter");
    if (filter) filter.oninput = () => queueApi.setGridOption("quickFilterText", filter.value);
  }

  function initLedger() {
    const container = document.getElementById("ledger-grid");
    const rows = readRows("ledger-data");
    if (!container || !rows || !window.agGrid || container.dataset.ready) return;
    container.dataset.ready = "1";
    ledgerApi = agGrid.createGrid(container, {
      theme: gridTheme(),
      rowData: rows,
      getRowId: (params) => String(params.data.id),
      domLayout: "autoHeight",
      animateRows: false,
      defaultColDef: { sortable: true, filter: true, resizable: true },
      columnDefs: [
        { headerName: "Key", field: "key_label", width: 84, cellClass: "mono" },
        { headerName: "Source", field: "source", width: 190, cellRenderer: cells.sourceCell },
        { headerName: "Kind", field: "kind", width: 120 },
        { headerName: "Fact", field: "claim", flex: 2, minWidth: 240, wrapText: true, autoHeight: true },
        { headerName: "Quoted from the source", field: "quote", flex: 2, minWidth: 240, wrapText: true, autoHeight: true, cellRenderer: cells.quoteCell },
        { headerName: "Supports", field: "supports_label", width: 200, cellRenderer: cells.supportsCell },
      ],
      onRowClicked: (event) => {
        const toggle = document.querySelector('[data-view-toggle="cards"]');
        if (toggle) toggle.click();
        window.setTimeout(() => {
          const card = document.getElementById("e" + event.data.id);
          if (!card) return;
          card.scrollIntoView({ block: "center", behavior: "smooth" });
          card.classList.add("flash");
          window.setTimeout(() => card.classList.remove("flash"), 1800);
        }, 120);
      },
    });
    const filter = document.getElementById("ledger-filter");
    if (filter) filter.oninput = () => ledgerApi.setGridOption("quickFilterText", filter.value);
  }

  window.RefundOpsGrids = {
    initQueue: initQueue,
    initLedger: initLedger,
    apis: () => ({ queue: queueApi, ledger: ledgerApi }),
  };
  // Both are safe to repeat: a grid is only built for a container that has not got one yet.
  document.addEventListener("DOMContentLoaded", initQueue);
  document.addEventListener("htmx:afterSettle", initQueue);
})();
