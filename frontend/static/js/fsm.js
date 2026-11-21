(function () {
  let renderSeq = 0;

  function ensureMermaid() {
    if (!window.mermaid) return null;
    if (!window.__tsMermaidInit) {
      window.mermaid.initialize({
        startOnLoad: false,
        theme: "dark",
        securityLevel: "loose",
        themeVariables: {
          primaryColor: "#232833",
          primaryTextColor: "#ece7db",
          lineColor: "#9aa194",
          secondaryColor: "#1d222d",
        },
      });
      window.__tsMermaidInit = true;
    }
    return window.mermaid;
  }

  window.TaskSenseFSM = {
    lastDefinition: null,
    lastPayload: null,
    render: async function (selector, definition) {
      const mermaid = ensureMermaid();
      const el = document.querySelector(selector);
      if (!el || !definition) return;
      this.lastDefinition = definition;
      if (!mermaid || !mermaid.render) {
        el.textContent = definition;
        return;
      }
      const id = "ts-fsm-" + (++renderSeq);
      try {
        const result = await mermaid.render(id, definition);
        el.innerHTML = result.svg || "";
        if (typeof result.bindFunctions === "function") result.bindFunctions(el);
      } catch (err) {
        el.textContent = definition + "\n\n" + err;
      }
    },
    rerender: function () {
      if (!this.lastDefinition) return;
      return this.render("#fsm-diagram", this.lastDefinition);
    },
    drawInto: async function (el, definition) {
      const mermaid = ensureMermaid();
      if (!el || !definition) return;
      if (!mermaid || !mermaid.render) {
        el.textContent = definition;
        return;
      }
      const id = "ts-draw-" + (++renderSeq);
      try {
        const result = await mermaid.render(id, definition);
        el.innerHTML = result.svg || "";
        if (typeof result.bindFunctions === "function") result.bindFunctions(el);
      } catch (err) {
        el.textContent = definition + "\n\n" + err;
      }
    },
    options: function (container, payload) {
      const host = document.querySelector(container);
      if (!host) return;
      this.lastPayload = payload || this.lastPayload;
      const source = payload || this.lastPayload || {};
      const options = source.observe_options || ["continue", "replan", "act", "wait", "respond"];
      const current = source.node === "observe" ? source.route : "";
      host.innerHTML = options
        .map(function (name) {
          const on = name === current ? " on" : "";
          return '<span class="opt' + on + '">' + name + "</span>";
        })
        .join("");
    },
  };
})();
