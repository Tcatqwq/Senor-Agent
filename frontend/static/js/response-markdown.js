(function () {
  const parser = window.marked && new window.marked.Marked({
    breaks: true,
    renderer: {
      html: ({ text }) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"),
    },
  });
  const allowedTags = new Set([
    "A", "BLOCKQUOTE", "BR", "CODE", "DEL", "EM", "H1", "H2", "H3", "H4", "H5", "H6",
    "HR", "IMG", "INPUT", "LI", "OL", "P", "PRE", "STRONG", "TABLE", "TBODY", "TD",
    "TH", "THEAD", "TR", "UL",
  ]);

  function allowedUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(value, document.baseURI);
      return ["http:", "https:", "mailto:"].includes(url.protocol) ? url : null;
    } catch {
      return null;
    }
  }

  function setLink(link, url) {
    link.href = url.href;
    if (url.protocol !== "mailto:") {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
  }

  function render(body, text) {
    body.classList.toggle("response-plain", !parser);
    if (!parser) {
      body.textContent = text;
      return;
    }

    // Keep model-generated markup inert until allowed elements and attributes are checked.
    const template = document.createElement("template");
    template.innerHTML = parser.parse(text);
    for (const el of template.content.querySelectorAll("*")) {
      const tag = el.tagName;
      if (!allowedTags.has(tag) || el.namespaceURI !== "http://www.w3.org/1999/xhtml") {
        el.replaceWith(document.createTextNode(el.textContent));
        continue;
      }
      const attrs = new Map([...el.attributes].map(({ name, value }) => [name, value]));
      for (const name of attrs.keys()) el.removeAttribute(name);
      if (tag === "A") {
        const url = allowedUrl(attrs.get("href"));
        if (url) setLink(el, url);
      } else if (tag === "IMG") {
        const url = allowedUrl(attrs.get("src"));
        const alt = attrs.get("alt") || "图片";
        if (url && url.origin === window.location.origin) {
          el.alt = alt;
          el.loading = "lazy";
          el.src = url.href;
        } else if (url) {
          const link = document.createElement("a");
          link.textContent = alt;
          setLink(link, url);
          el.replaceWith(link);
        } else {
          el.replaceWith(document.createTextNode(alt));
        }
      } else if (tag === "OL" && /^\d+$/.test(attrs.get("start") || "")) {
        el.setAttribute("start", attrs.get("start"));
      } else if (tag === "INPUT") {
        el.type = "checkbox";
        el.disabled = true;
        el.checked = attrs.has("checked");
      }
    }
    body.replaceChildren(template.content);
  }

  window.TaskSenseResponseMarkdown = { render };
})();
