(function () {
  const body = document.getElementById("browse-body");
  if (!body) return;
  const modalitySel = document.getElementById("filter-modality");
  const dateSel = document.getElementById("filter-date");
  const toolSel = document.getElementById("filter-tool");
  let currentTab = "sensor";
  let virtualJobTimer = null;

  function qs() {
    const params = new URLSearchParams();
    if (modalitySel.value) params.set("modality", modalitySel.value);
    if (dateSel.value) params.set("date", dateSel.value);
    if (toolSel.value) params.set("tool", toolSel.value);
    const text = params.toString();
    return text ? "?" + text : "";
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fillSelect(select, values, extraLabel) {
    const current = select.value;
    select.innerHTML = '<option value="">' + (extraLabel || "全部") + "</option>";
    (values || []).forEach(function (value) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = value;
      select.appendChild(opt);
    });
    if (current) select.value = current;
  }

  function bytes(size) {
    if (!size) return "";
    if (size < 1024) return size + " B";
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
    return (size / (1024 * 1024)).toFixed(1) + " MB";
  }

  function thumb(path) {
    const src = "/api/thumbnail?path=" + encodeURIComponent(path);
    return '<img class="thumb" alt="" src="' + src + '" onerror="this.style.display=\'none\'">';
  }

  function renderSensor(data) {
    const rows = (data.items || [])
      .map(function (item) {
        return (
          "<tr><td>" +
          thumb(item.path) +
          "</td><td>" +
          item.modality +
          "</td><td>" +
          escapeHtml((item.date || "") + " " + (item.hour || "")) +
          "</td><td>" +
          escapeHtml(item.name) +
          '<div class="kv">' +
          escapeHtml(item.path) +
          "</div></td><td>" +
          bytes(item.size) +
          "</td></tr>"
        );
      })
      .join("");
    body.innerHTML =
      '<p class="muted">共 ' +
      data.total +
      " 条" +
      (data.truncated ? "（已截断显示）" : "") +
      '</p><div class="table-wrap"><table><thead><tr><th>预览</th><th>模态</th><th>时间</th><th>文件</th><th>大小</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="5">没有匹配的数据</td></tr>') +
      "</tbody></table></div>";
  }

  function renderCache(data) {
    const exec = data.execution || {};
    const rows = (exec.items || [])
      .map(function (item) {
        const labels = (item.labels || []).map(function (label) {
          return '<span class="chip">' + escapeHtml(label) + "</span>";
        }).join(" ");
        const outputs = (item.outputs || []).slice(0, 6).map(function (path) {
          return '<div class="kv">' + escapeHtml(path) + "</div>";
        }).join("");
        return (
          "<tr><td>" +
          item.tool +
          "</td><td>" +
          escapeHtml((item.date || "") + " " + (item.hour || "")) +
          "</td><td>" +
          escapeHtml(item.modality) +
          "</td><td>" +
          labels +
          outputs +
          '<div class="kv">' +
          escapeHtml(item.source) +
          "</div></td></tr>"
        );
      })
      .join("");
    const extras = ["embedding", "assessment", "flops"]
      .map(function (key) {
        const block = data[key] || {};
        return (
          '<article class="tool-card"><h3>' +
          key +
          " cache</h3><div class='kv'>" +
          (block.path || "") +
          " · " +
          (block.count || 0) +
          " keys</div><div>" +
          (block.sample_queries || block.keys || [])
            .slice(0, 12)
            .map(function (item) {
              return '<span class="chip">' + escapeHtml(item) + "</span>";
            })
            .join(" ") +
          "</div>" +
          (block.note ? '<p class="muted">' + block.note + "</p>" : "") +
          "</article>"
        );
      })
      .join("");
    body.innerHTML =
      '<p class="muted">execution cache ' +
      (exec.total || 0) +
      " 条 · " +
      (exec.path || "") +
      '</p><div class="table-wrap"><table><thead><tr><th>工具</th><th>时间</th><th>模态</th><th>结果</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="4">没有匹配的缓存</td></tr>') +
      "</tbody></table></div>" +
      extras;
  }

  function renderIntermediate(data) {
    const rows = (data.items || [])
      .map(function (item) {
        return (
          "<tr><td>" +
          thumb(item.path) +
          "</td><td>" +
          (item.folder || "") +
          "</td><td>" +
          escapeHtml((item.date || "") + " " + (item.hour || "")) +
          "</td><td>" +
          escapeHtml(item.name) +
          '<div class="kv">' +
          escapeHtml(item.path) +
          "</div></td></tr>"
        );
      })
      .join("");
    body.innerHTML =
      '<p class="muted">' +
      (data.path || "") +
      " · " +
      data.total +
      ' 条</p><div class="table-wrap"><table><thead><tr><th>预览</th><th>目录</th><th>时间</th><th>文件</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="4">没有中间结果</td></tr>') +
      "</tbody></table></div>";
  }

  function renderTools(data) {
    const tools = (data.tools || [])
      .map(function (tool) {
        return (
          '<article class="tool-card"><h3>' +
          escapeHtml(tool.tool_name) +
          ' <span class="chip">' +
          escapeHtml(tool.category) +
          "</span> " +
          (tool.modality ? '<span class="chip">' + escapeHtml(tool.modality) + "</span>" : "") +
          "</h3><p>" +
          escapeHtml(tool.description || "") +
          "</p><div class='kv'>implementation: " +
          escapeHtml(tool.implementation || "未绑定") +
          "</div><div class='kv'>input: " +
          escapeHtml(JSON.stringify(tool.input || {})) +
          "</div><div class='kv'>output: " +
          escapeHtml(JSON.stringify(tool.output || {})) +
          "</div>" +
          (tool.checkpoint
            ? '<div class="kv">checkpoint: ' +
              escapeHtml(tool.checkpoint) +
              (tool.checkpoint_exists ? " (exists)" : " (missing)") +
              "</div>"
            : "") +
          "</article>"
        );
      })
      .join("");
    const models = (data.models || [])
      .map(function (model) {
        return (
          '<article class="tool-card"><h3>' +
          model.name +
          "</h3><div class='kv'>tool: " +
          (model.tool_name || "-") +
          "</div><div class='kv'>" +
          model.path +
          (model.exists ? " · exists · " + model.size + " bytes" : " · missing") +
          "</div></article>"
        );
      })
      .join("");
    const notes = (data.runtime_notes || [])
      .map(function (note) {
        return '<p class="muted">' + note.name + ": " + note.note + "</p>";
      })
      .join("");
    body.innerHTML = "<h2>工具</h2>" + tools + "<h2>模型 checkpoint</h2>" + models + notes;
  }

  const titleEl = document.getElementById("browse-title");
  const subtitleEl = document.getElementById("browse-subtitle");
  const realFilters = document.getElementById("real-filters");
  const browseTabs = document.getElementById("browse-tabs");
  let browseMode = "real";

  async function load() {
    if (browseMode !== "virtual") {
      stopVirtualJobPoll();
    }
    if (browseMode === "virtual") {
      await loadVirtual();
      return;
    }
    body.textContent = "加载中…";
    try {
      if (currentTab === "sensor") renderSensor(await (await fetch("/api/browse/sensor" + qs())).json());
      else if (currentTab === "cache") renderCache(await (await fetch("/api/browse/cache" + qs())).json());
      else if (currentTab === "intermediate")
        renderIntermediate(await (await fetch("/api/browse/intermediate" + qs())).json());
      else renderTools(await (await fetch("/api/browse/tools")).json());
    } catch (err) {
      body.textContent = "加载失败: " + err.message;
    }
  }

  const EVENTS_EXAMPLE = [
    {
      date: "2023-11-16",
      start_hour: 1,
      end_hour: 2,
      start: "2023-11-16 01:00",
      end: "2023-11-16 02:00",
      modality: "thermal_video",
      har_label: "Sitting and making a phone call",
      note: "仅该模态在该时段强制使用该 HAR label；省略 modality 则所有模态生效",
    },
  ];

  function depText(deps) {
    return (deps || [])
      .map(function (edge) {
        if (Array.isArray(edge)) return edge.join(" -> ");
        return String(edge);
      })
      .join("\n");
  }

  function eventsText(events) {
    return JSON.stringify(events || [], null, 2);
  }

  function harModFields(cfg) {
    const byMod = cfg.har_labels_by_modality || {};
    const mods = cfg.modalities || [];
    if (!mods.length) return "";
    return mods
      .map(function (mod) {
        const key = String(mod || "").trim();
        if (!key) return "";
        const labels = byMod[key] || [];
        return (
          '<label class="field"><span>HAR labels · ' +
          escapeHtml(key) +
          "（每行一个；留空则使用上方全局列表）</span><textarea name=\"har_mod_" +
          escapeHtml(key) +
          '" rows="4">' +
          escapeHtml(labels.join("\n")) +
          "</textarea></label>"
        );
      })
      .join("");
  }

  function readVirtualForm() {
    const form = document.getElementById("virtual-form");
    if (!form) return null;
    const get = function (name) {
      const el = form.querySelector('[name="' + name + '"]');
      return el ? el.value : "";
    };
    const checked = function (name) {
      const el = form.querySelector('[name="' + name + '"]');
      return !!(el && el.checked);
    };
    let events = [];
    const rawEvents = get("events").trim();
    if (rawEvents) {
      try {
        events = JSON.parse(rawEvents);
      } catch (err) {
        throw new Error("events 必须是 JSON 数组");
      }
    }
    const dependencies = get("dependencies")
      .split("\n")
      .map(function (line) {
        return line.trim();
      })
      .filter(Boolean)
      .map(function (line) {
        const parts = line.split("->");
        if (parts.length < 2) return null;
        return [parts[0].trim(), parts.slice(1).join("->").trim()];
      })
      .filter(Boolean);
    const har_labels_by_modality = {};
    form.querySelectorAll("textarea[name^='har_mod_']").forEach(function (el) {
      const key = el.name.slice("har_mod_".length);
      const labels = el.value
        .split("\n")
        .map(function (item) {
          return item.trim();
        })
        .filter(Boolean);
      if (key) har_labels_by_modality[key] = labels;
    });
    return {
      virtual_modality: checked("virtual_modality"),
      use_cache: checked("use_cache"),
      modalities: get("modalities")
        .split(/[,\n]/)
        .map(function (item) {
          return item.trim();
        })
        .filter(Boolean),
      date_range: { start: get("date_start"), end: get("date_end") },
      har_labels: get("har_labels")
        .split("\n")
        .map(function (item) {
          return item.trim();
        })
        .filter(Boolean),
      har_labels_by_modality: har_labels_by_modality,
      dependencies: dependencies,
      environment_info: get("environment_info"),
      events: events,
    };
  }

  function setModalityDisabled(disabled) {
    const block = document.getElementById("virtual-modality-fields");
    if (!block) return;
    block.classList.toggle("is-disabled", disabled);
    block.querySelectorAll("input, textarea, select, button").forEach(function (el) {
      el.disabled = disabled;
    });
    const generated = document.getElementById("virtual-generated");
    if (generated) generated.classList.toggle("is-disabled", disabled);
  }

  function renderVirtual(data) {
    const cfg = (data && data.config) || {};
    const catalog = (data && data.catalog) || {};
    const cache = (data && data.cache) || {};
    const virtMod = !!cfg.virtual_modality;
    const enabled = !!cfg.enabled;
    const grammar = (catalog.grammar_rules || []).map(function (rule) {
      return '<div class="kv">' + escapeHtml(rule) + "</div>";
    }).join("");
    const tools = [];
    const vocab = catalog.vocabulary || {};
    Object.keys(vocab).forEach(function (category) {
      (vocab[category] || []).forEach(function (tool) {
        tools.push(
          '<article class="tool-card"><h3>' +
            escapeHtml(tool.tool_name || "") +
            ' <span class="chip">' +
            escapeHtml(category) +
            "</span>" +
            (tool.modality ? ' <span class="chip">' + escapeHtml(tool.modality) + "</span>" : "") +
            "</h3><p>" +
            escapeHtml(tool.description || "") +
            "</p></article>"
        );
      });
    });
    const configDeps = cfg.dependencies || [];
    const catalogDeps = catalog.dependencies || [];
    const shownDeps = configDeps.length ? configDeps : catalogDeps;
    const depHint = configDeps.length
      ? "每行 from -> to。保存后作为下次生成约束。"
      : "配置为空，以下为当前 grammar 中的依赖；保存后写入配置，作为下次生成约束。可留空由 LLM 按模态生成。";
    const vocabJson =
      vocab && Object.keys(vocab).length
        ? '<pre class="log json">' + escapeHtml(JSON.stringify(vocab, null, 2)) + "</pre>"
        : '<p class="muted">无</p>';
    const depPreview = (catalogDeps.length ? catalogDeps : catalog.grammar_rules || [])
      .map(function (edge) {
        const line = Array.isArray(edge) ? edge.join(" -> ") : String(edge);
        return '<div class="kv">' + escapeHtml(line) + "</div>";
      })
      .join("");
    const cacheRows = (cache.items || [])
      .map(function (item) {
        return (
          "<tr><td>" +
          escapeHtml(item.tool || "") +
          "</td><td>" +
          (item.labels || []).map(function (label) {
            return '<span class="chip">' + escapeHtml(label) + "</span>";
          }).join(" ") +
          '</td><td class="kv">' +
          escapeHtml(JSON.stringify(item.output || {})) +
          "</td></tr>"
        );
      })
      .join("");
    body.innerHTML =
      (enabled
        ? ""
        : '<p class="status err">主开关未开启：请先在「参数设置 → LLM虚拟环境」中进入模式，下面的参数仍可预先配置。</p>') +
      '<form id="virtual-form" class="virtual-form">' +
      '<label class="check"><input type="checkbox" name="virtual_modality"' +
      (virtMod ? " checked" : "") +
      "> 虚拟模态（开启后由 LLM 生成 vocabulary / grammar / tool list）</label>" +
      '<p class="hint">关闭虚拟模态时：工具、依赖、grammar 与真实环境一致，仅调用结果由 LLM 生成。日期范围与整体环境信息始终生效。</p>' +
      '<label class="check"><input type="checkbox" name="use_cache"' +
      (cfg.use_cache !== false ? " checked" : "") +
      "> 使用虚拟执行 cache</label>" +
      '<p class="hint">关闭后每次调用都重新向 LLM 要结果，不读写独立 cache。</p>' +
      '<div class="grid-2">' +
      '<label class="field"><span>数据日期范围 · 开始</span><input name="date_start" type="date" value="' +
      escapeHtml((cfg.date_range || {}).start || "") +
      '"></label>' +
      '<label class="field"><span>数据日期范围 · 结束</span><input name="date_end" type="date" value="' +
      escapeHtml((cfg.date_range || {}).end || "") +
      '"></label>' +
      "</div>" +
      '<label class="field"><span>整体环境信息</span><textarea name="environment_info" rows="4" placeholder="例如：2023-11-16 1点~2点之间有人打电话">' +
      escapeHtml(cfg.environment_info || "") +
      "</textarea></label>" +
      '<label class="field"><span>硬约束 events（JSON 数组）</span><textarea name="events" rows="8">' +
      escapeHtml(eventsText(cfg.events)) +
      "</textarea></label>" +
      '<p class="hint">完整字段示例（date+start_hour/end_hour 与 start/end 两套时间均可；有 date+start_hour 时优先前者。省略 modality 则对所有模态生效）：</p>' +
      '<pre class="example-block">' +
      escapeHtml(JSON.stringify(EVENTS_EXAMPLE, null, 2)) +
      "</pre>" +
      '<div id="virtual-modality-fields">' +
      '<label class="field"><span>LLM 可提供的模态（逗号或换行）</span><textarea name="modalities" rows="2">' +
      escapeHtml((cfg.modalities || []).join(", ")) +
      "</textarea></label>" +
      '<label class="field"><span>依赖关系（' +
      escapeHtml(depHint) +
      "）</span><textarea name=\"dependencies\" rows=\"5\">" +
      escapeHtml(depText(shownDeps)) +
      "</textarea></label>" +
      '<label class="field"><span>全局 HAR label（每行一个；分模态未填时作为回退）</span><textarea name="har_labels" rows="4">' +
      escapeHtml((cfg.har_labels || []).join("\n")) +
      "</textarea></label>" +
      harModFields(cfg) +
      "</div>" +
      '<div class="composer-actions virtual-actions">' +
      '<button type="button" class="btn" id="btn-virtual-rebuild">重新生成 vocab/grammar</button>' +
      '<button type="button" class="btn primary" id="btn-virtual-save">保存</button>' +
      "</div>" +
      '<p class="status" id="virtual-status"></p>' +
      "</form>" +
      '<section id="virtual-generated"><h2>生成物预览</h2>' +
      '<p class="muted">' +
      (catalog.generated_ready ? "已生成 · " + escapeHtml(catalog.generated_dir || "") : "尚未生成") +
      (catalog.missing && catalog.missing.length
        ? " · 缺少: " + escapeHtml(catalog.missing.join(", "))
        : "") +
      "</p>" +
      "<h3>Grammar</h3>" +
      (grammar || '<p class="muted">无</p>') +
      "<h3>Dependencies</h3>" +
      (depPreview || '<p class="muted">无</p>') +
      "<h3>Vocabulary</h3>" +
      vocabJson +
      "<h3>Tools</h3>" +
      (tools.join("") || '<p class="muted">无</p>') +
      "</section>" +
      '<div class="cache-h"><h2>虚拟执行 cache</h2>' +
      '<button type="button" class="btn danger" id="btn-clear-virtual-cache">清空虚拟执行 cache</button></div>' +
      '<p class="muted">' +
      escapeHtml(cache.path || "") +
      " · " +
      (cache.total || 0) +
      " 条。重新生成 vocabulary / grammar 成功后也会自动清空。</p>" +
      '<div class="table-wrap"><table><thead><tr><th>工具</th><th>标签</th><th>输出</th></tr></thead><tbody>' +
      (cacheRows || '<tr><td colspan="3">cache 为空</td></tr>') +
      "</tbody></table></div>";

    setModalityDisabled(!virtMod);
    const toggle = document.querySelector('#virtual-form [name="virtual_modality"]');
    if (toggle) {
      toggle.addEventListener("change", function () {
        setModalityDisabled(!toggle.checked);
        const rebuildBtn = document.getElementById("btn-virtual-rebuild");
        if (rebuildBtn) rebuildBtn.disabled = !toggle.checked;
      });
    }
    document.getElementById("btn-virtual-save").addEventListener("click", function () {
      saveVirtual(true).catch(function (err) {
        const status = document.getElementById("virtual-status");
        if (status) {
          status.className = "status err";
          status.textContent = err.message;
        }
      });
    });
    const rebuildBtn = document.getElementById("btn-virtual-rebuild");
    if (rebuildBtn) {
      rebuildBtn.disabled = !virtMod || (data.rebuild_job && data.rebuild_job.status === "running");
      rebuildBtn.addEventListener("click", function () {
        rebuildVirtual();
      });
    }
    const clearBtn = document.getElementById("btn-clear-virtual-cache");
    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        if (!window.confirm("确定清空 LLM 虚拟环境执行 cache 吗？")) return;
        fetch("/api/llm-virtual-env/cache/clear", { method: "POST" })
          .then(function (res) {
            return res.json().then(function (payload) {
              if (!res.ok) throw new Error(payload.detail || "清空失败");
              return payload;
            });
          })
          .then(function () {
            return loadVirtual();
          })
          .catch(function (err) {
            const status = document.getElementById("virtual-status");
            if (status) {
              status.className = "status err";
              status.textContent = err.message;
            }
          });
      });
    }
    applyJobStatus(data.rebuild_job);
    if (data.rebuild_job && data.rebuild_job.status === "running") {
      pollVirtualJob();
    }
  }

  function applyJobStatus(job) {
    const status = document.getElementById("virtual-status");
    const saveBtn = document.getElementById("btn-virtual-save");
    const rebuildBtn = document.getElementById("btn-virtual-rebuild");
    const running = !!(job && job.status === "running");
    if (saveBtn) saveBtn.disabled = running;
    if (rebuildBtn) {
      const virtMod = document.querySelector('#virtual-form [name="virtual_modality"]');
      rebuildBtn.disabled = running || (virtMod && !virtMod.checked);
    }
    if (!status) return;
    if (running) {
      status.className = "status";
      status.textContent = "正在由 LLM 生成 vocabulary / grammar / tool list，请稍候…";
      return;
    }
    if (job && job.status === "error") {
      status.className = "status err";
      status.textContent = "生成失败: " + (job.error || "未知错误");
      return;
    }
    if (job && job.status === "ok") {
      status.className = "status";
      status.textContent =
        "已重新生成 vocabulary / grammar / tool list" +
        (job.result && job.result.cache_cleared ? "，并已清空虚拟环境 cache" : "");
      return;
    }
    if (job && job.status === "skipped") {
      status.className = "status";
      status.textContent = "已保存（生成物未变化）";
    }
  }

  function stopVirtualJobPoll() {
    if (virtualJobTimer) {
      clearInterval(virtualJobTimer);
      virtualJobTimer = null;
    }
  }

  function pollVirtualJob() {
    if (virtualJobTimer) return;
    virtualJobTimer = setInterval(async function () {
      try {
        if (browseMode !== "virtual") {
          stopVirtualJobPoll();
          return;
        }
        const data = await (await fetch("/api/llm-virtual-env/job")).json();
        const job = data.rebuild_job || {};
        applyJobStatus(job);
        if (job.status && job.status !== "running") {
          stopVirtualJobPoll();
          if (job.status === "ok") {
            renderVirtual(data);
            applyJobStatus(job);
          }
        }
      } catch (err) {
        stopVirtualJobPoll();
        const status = document.getElementById("virtual-status");
        if (status) {
          status.className = "status err";
          status.textContent = "查询生成任务失败: " + err.message;
        }
      }
    }, 1500);
  }

  async function loadVirtual() {
    body.textContent = "加载中…";
    try {
      const data = await (await fetch("/api/browse/virtual")).json();
      renderVirtual(data);
    } catch (err) {
      body.textContent = "加载失败: " + err.message;
    }
  }

  async function saveVirtual(rebuild) {
    const payload = readVirtualForm();
    payload.rebuild = !!rebuild;
    const res = await fetch("/api/llm-virtual-env", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
    if (!data.ok) throw new Error(data.rebuild_error || "保存失败");
    renderVirtual(data);
    if (data.rebuild_job && data.rebuild_job.status === "running") {
      pollVirtualJob();
    } else {
      applyJobStatus(data.rebuild_job || (data.rebuild && data.rebuild.skipped ? { status: "skipped" } : null));
    }
    return data;
  }

  async function rebuildVirtual() {
    try {
      await saveVirtual(false);
      const res = await fetch("/api/llm-virtual-env/rebuild", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
      renderVirtual(data);
      applyJobStatus(data.rebuild_job);
      if (data.rebuild_job && data.rebuild_job.status === "running") {
        pollVirtualJob();
      }
    } catch (err) {
      const next = document.getElementById("virtual-status");
      if (next) {
        next.className = "status err";
        next.textContent = err.message;
      }
    }
  }

  function setBrowseMode(mode) {
    browseMode = mode;
    document.querySelectorAll("[data-browse-mode]").forEach(function (btn) {
      btn.classList.toggle("on", btn.getAttribute("data-browse-mode") === mode);
    });
    const virtual = mode === "virtual";
    if (titleEl) titleEl.textContent = virtual ? "资源浏览 · LLM虚拟环境" : "资源浏览";
    if (subtitleEl)
      subtitleEl.textContent = virtual
        ? "配置虚拟传感器世界：模态、依赖、日期范围、HAR 标签与环境信息。调用结果由 LLM 按这些约束生成。"
        : "按模态与时间查看传感器数据、执行缓存、中间结果，以及可用工具与模型。";
    if (realFilters) realFilters.hidden = virtual;
    if (browseTabs) browseTabs.hidden = virtual;
    load();
  }

  async function initFilters() {
    const data = await (await fetch("/api/browse/filters")).json();
    fillSelect(modalitySel, data.modalities);
    fillSelect(dateSel, data.dates);
    fillSelect(toolSel, data.cache_tools);
    await initRealCacheToggle();
  }

  async function initRealCacheToggle() {
    const box = document.getElementById("real-use-cache");
    if (!box) return;
    try {
      const cfg = await (await fetch("/api/config")).json();
      box.checked = !!(cfg.execution_settings && cfg.execution_settings.use_cache);
    } catch (err) {}
    box.addEventListener("change", async function () {
      try {
        const res = await fetch("/api/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ execution_settings: { use_cache: box.checked } }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "保存失败");
      } catch (err) {
        box.checked = !box.checked;
        window.alert(err.message);
      }
    });
  }

  document.querySelectorAll("#browse-tabs .tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll("#browse-tabs .tab").forEach(function (item) {
        item.classList.toggle("on", item === tab);
      });
      currentTab = tab.getAttribute("data-tab");
      load();
    });
  });
  [modalitySel, dateSel, toolSel].forEach(function (el) {
    el.addEventListener("change", load);
  });
  document.querySelectorAll("[data-browse-mode]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setBrowseMode(btn.getAttribute("data-browse-mode"));
    });
  });

  initFilters().then(load);
  document.addEventListener("tasksense:workspace", function (evt) {
    if (evt.detail === "browse") load();
  });
})();
