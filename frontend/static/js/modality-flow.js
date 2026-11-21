(function () {
  const board = document.getElementById("modality-board");
  const scroll = document.getElementById("modality-scroll");
  const detail = document.getElementById("modality-detail");
  const caption = document.getElementById("modality-caption");
  const empty = document.getElementById("modality-empty");
  const legend = document.getElementById("modality-legend");
  const roundSelect = document.getElementById("modality-round");
  const sources = document.querySelectorAll("[data-modality-source]");
  const rounds = new Map();
  let source = "live";
  let demo;
  let demoError = false;
  let currentRound = null;
  let selected = null;
  let liveStatus = empty.textContent.trim();
  let liveRunning = false;
  let renderQueued = false;
  let redrawEdges = function () {};

  const statuses = {
    selected: ["已选用", "该路径执行成功，结果用于回复。"],
    available: ["已取得数据", "查询返回了该时段的媒体。"],
    called: ["已调用", "该工具在本次运行中完成调用。"],
    missing: ["无数据", "该时段未取得对应模态的数据。"],
    skipped: ["已跳过", "上游工具没有可继续处理的结果。"],
    quality: ["质量不足", "质量筛选未通过。"],
    invalid: ["无有效结果", "该工具或路径未产生可继续使用的结果。"],
    error: ["执行错误", "该候选路径执行出错。"],
    running: ["处理中", "正在执行此次调用。"],
    checking: ["检查质量", "正在评估视频质量。"],
  };
  const edgeLabels = { dependency: "工具依赖", continuity: "延续", change: "模态切换", fallback: "候选路径回退" };

  function element(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text) item.textContent = text;
    return item;
  }

  function hours(node) {
    if (!node.date) return "未记录时间";
    const clock = (hour) => String(hour).padStart(2, "0") + ":00";
    return clock(node.start) + "–" + clock(node.end);
  }

  function modalityName(modality) {
    return modality === "rgb" ? "RGB" : modality.charAt(0).toUpperCase() + modality.slice(1);
  }

  function statusChip(node) {
    return element("span", "flow-status flow-" + node.status, statuses[node.status][0]);
  }

  function buildBoard(graph, covers) {
    const columnOf = (node) => [node.date, node.start, node.end].join("/");
    const ordered = graph.nodes.slice().sort((a, b) =>
      (a.date || "9999").localeCompare(b.date || "9999") || a.start - b.start || a.end - b.end
    );
    const columns = new Map();
    const modalities = [...new Set(ordered.map((node) => node.modality))];
    modalities.sort((a, b) => (a === "rgb" ? -1 : b === "rgb" ? 1 : a.localeCompare(b)));
    for (const node of ordered) {
      const key = columnOf(node);
      if (!columns.has(key)) columns.set(key, []);
      columns.get(key).push(node);
    }

    board.style.setProperty("--flow-columns", columns.size);
    const edgesLayer = element("div", "flow-edges");
    edgesLayer.setAttribute("aria-hidden", "true");
    board.append(edgesLayer, element("div", "flow-lane flow-axis", "时间 →"));
    modalities.forEach(function (modality, row) {
      const lane = element("div", "flow-lane", modalityName(modality));
      lane.style.gridRow = row + 2;
      board.append(lane);
    });

    const cards = new Map();
    const columnIndexes = new Map();
    let columnIndex = 2;
    for (const nodes of columns.values()) {
      const first = nodes[0];
      const heading = element("div", "flow-time");
      if (first.date) heading.append(element("span", "flow-date", first.date));
      heading.append(element("strong", "", hours(first)));
      heading.style.gridColumn = columnIndex;
      board.append(heading);

      const cells = new Map();
      for (const node of nodes) {
        if (!cells.has(node.modality)) {
          const cell = element("div", "flow-cell");
          cell.style.gridArea = (modalities.indexOf(node.modality) + 2) + " / " + columnIndex;
          board.append(cell);
          cells.set(node.modality, cell);
        }
        const card = element("button", "flow-node flow-" + node.status);
        card.type = "button";
        card.setAttribute("aria-label", [modalityName(node.modality), node.date, hours(node), statuses[node.status][0], node.tool].filter(Boolean).join("，"));
        if (node.cover) {
          const key = JSON.stringify([node.id, node.cover]);
          let image = covers.get(key);
          if (!image) {
            image = element("img", "flow-cover");
            image.dataset.coverKey = key;
            image.alt = "";
            image.loading = "lazy";
            image.src = "/api/thumbnail?" + new URLSearchParams({ path: node.cover });
            image.addEventListener("error", () => { image.hidden = true; });
          }
          card.append(image);
        }
        const content = element("span", "flow-node-body");
        content.append(element("strong", "", modalityName(node.modality)), statusChip(node));
        if (node.tool) content.append(element("span", "flow-tool", node.tool));
        card.append(content);
        card.addEventListener("click", () => selectNode(node));
        cells.get(node.modality).append(card);
        cards.set(node.id, card);
        columnIndexes.set(node.id, columnIndex);
      }
      columnIndex += 1;
    }

    function selectNode(node) {
      selected = node || null;
      for (const [id, card] of cards) card.setAttribute("aria-pressed", String(id === selected?.id));
      detail.hidden = !node;
      if (!node) return;
      const title = [node.date, hours(node), modalityName(node.modality)].filter(Boolean).join(" · ");
      detail.replaceChildren(element("strong", "", title), statusChip(node));
      const tool = node.tool ? node.tool + "。" : "";
      detail.append(element("p", "muted", tool + (node.reason || statuses[node.status][1])));
      if (node.calls?.length) {
        const calls = element("ul", "flow-calls");
        for (const call of node.calls) {
          const item = element("li");
          item.append(element("code", "", call.tool), statusChip(call));
          calls.append(item);
        }
        detail.append(calls);
      }
    }

    const lines = graph.edges.map(function (edge) {
      const line = element("div", "flow-edge flow-edge-" + edge.kind);
      edgesLayer.append(line);
      return [edge, line];
    });
    redrawEdges = function () {
      if (!board.offsetWidth) return;
      const origin = board.getBoundingClientRect();
      for (const [edge, line] of lines) {
        const from = cards.get(edge.from).getBoundingClientRect();
        const to = cards.get(edge.to).getBoundingClientRect();
        const direction = columnIndexes.get(edge.to) - columnIndexes.get(edge.from);
        const x = direction > 0 ? from.right : direction < 0 ? from.left : (from.left + from.right) / 2;
        const endX = direction > 0 ? to.left : direction < 0 ? to.right : (to.left + to.right) / 2;
        const down = to.top > from.top;
        const y = direction ? (from.top + from.bottom) / 2 : down ? from.bottom : from.top;
        const endY = direction ? (to.top + to.bottom) / 2 : down ? to.top : to.bottom;
        line.style.left = x - origin.left + "px";
        line.style.top = y - origin.top + "px";
        line.style.width = Math.hypot(endX - x, endY - y) + "px";
        line.style.transform = "rotate(" + Math.atan2(endY - y, endX - x) + "rad)";
      }
    };
    if (selected) {
      const coversSelection = (node) => node.modality === selected.modality && node.date === selected.date &&
        (!node.date || (node.start <= selected.start && node.end > selected.start));
      selectNode(ordered.find((node) => node.id === selected.id && coversSelection(node)) || ordered.find(coversSelection));
    } else {
      selectNode(null);
    }
    redrawEdges();
  }

  function render(preserveScroll = false) {
    if (source === "live" && !rounds.has(currentRound)) {
      currentRound = [...rounds.keys()].at(-1) ?? null;
    }
    const graph = source === "demo" ? demo : rounds.get(currentRound);
    const hasGraph = !!(graph && graph.nodes.length);
    const left = scroll.scrollLeft;
    const follow = preserveScroll && liveRunning && !selected && scroll.clientWidth > 0 &&
      scroll.scrollWidth - (left + scroll.clientWidth) < 24;
    const covers = new Map([...board.querySelectorAll("img[data-cover-key]")].map((img) => [img.dataset.coverKey, img]));
    document.getElementById("modality-round-label").hidden = source !== "live" || rounds.size < 2;
    roundSelect.replaceChildren(...[...rounds.keys()].map((round) => new Option("第 " + (round + 1) + " 轮", round)));
    roundSelect.value = currentRound;
    caption.textContent = source === "demo"
      ? "log_demo1 · 2023-11-16 · 相邻时段按相同决策合并"
      : hasGraph ? (liveRunning ? "实时更新 · " : "") + (
        graph.adaptive ? "按实际候选决策合并相邻时段 · 点击节点查看调用详情"
        : (graph.virtual ? "LLM 虚拟环境 · " : "") + "按媒体时段排列 · 连线表示工具依赖"
      ) : "";
    empty.hidden = hasGraph;
    empty.textContent = source === "demo"
      ? demoError ? "日志回放加载失败，请重新选择 Demo 1 日志。" : "正在加载日志回放…"
      : liveStatus;
    scroll.hidden = !hasGraph;
    legend.hidden = !hasGraph;
    detail.hidden = true;
    board.replaceChildren();
    legend.replaceChildren();
    redrawEdges = function () {};
    if (!hasGraph) return;

    for (const kind of new Set(graph.edges.map((edge) => edge.kind))) {
      const key = element("span", "flow-legend-key", edgeLabels[kind]);
      key.prepend(element("i", "flow-edge-sample flow-edge-" + kind));
      legend.append(key);
    }
    legend.hidden = !legend.childNodes.length;
    buildBoard(graph, covers);
    if (preserveScroll) scroll.scrollLeft = follow ? scroll.scrollWidth : left;
  }

  function scheduleLiveRender() {
    if (source !== "live" || renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(function () {
      renderQueued = false;
      if (source === "live") render(true);
    });
  }

  const observer = new ResizeObserver(() => redrawEdges());
  observer.observe(board);
  roundSelect.addEventListener("change", function () {
    currentRound = Number(roundSelect.value);
    selected = null;
    scroll.scrollLeft = 0;
    render();
  });
  sources.forEach(function (button) {
    button.addEventListener("click", async function () {
      source = button.dataset.modalitySource;
      selected = null;
      scroll.scrollLeft = 0;
      for (const item of sources) {
        item.classList.toggle("on", item === button);
        item.setAttribute("aria-pressed", String(item === button));
      }
      if (source !== "demo" || demo) {
        render();
        return;
      }
      demoError = false;
      render();
      try {
        const response = await fetch("/api/modality-flow/demo");
        if (!response.ok) throw new Error("Demo unavailable");
        demo = await response.json();
      } catch (err) {
        demoError = true;
      }
      if (source === "demo") render();
    });
  });

  window.TaskSenseModalityFlow = {
    reset: function () {
      rounds.clear();
      currentRound = null;
      liveStatus = "等待本次运行的模态调用…";
      liveRunning = true;
      if (source === "live") {
        selected = null;
        scroll.scrollLeft = 0;
        render();
      }
    },
    setStatus: function (message) {
      liveStatus = message;
      liveRunning = false;
      scheduleLiveRender();
    },
    update: function (graph) {
      const round = graph.round || 0;
      if (!rounds.has(round)) {
        currentRound = round;
        if (source === "live") selected = null;
      }
      rounds.set(round, graph);
      if (round === currentRound) scheduleLiveRender();
    },
  };
})();
