(function () {
  const userView = document.getElementById("view-user");
  const debugView = document.getElementById("view-debug");
  const modalityView = document.getElementById("view-modality");
  const fullViewToggle = document.getElementById("full-view-toggle");
  const userStream = document.getElementById("user-stream");
  const userEmpty = document.getElementById("user-empty");
  const userStage = document.getElementById("user-stage");
  const debugLog = document.getElementById("debug-log");
  const debugState = document.getElementById("debug-state");
  const fsmCaption = document.getElementById("fsm-caption");
  const form = document.getElementById("run-form");
  const queryInput = document.getElementById("query-input");
  const tickLoop = document.getElementById("tick-loop");
  const runBtn = document.getElementById("btn-run");
  const stopBtn = document.getElementById("btn-stop");
  const settingsModal = document.getElementById("settings-modal");
  let eventSource = null;
  const userCards = new Map();
  let currentRunId = null;
  let runClosed = true;
  let lastStage = null;
  let lastPlan = null;

  function setWorkspace(ws, push) {
    const next = ws === "browse" ? "browse" : "main";
    const prev = document.body.dataset.workspace === "browse" ? "browse" : "main";
    document.body.dataset.workspace = next;
    const browseView = document.getElementById("view-browse");
    if (browseView) browseView.hidden = next !== "browse";
    if (push !== false) {
      const url = next === "browse" ? "/browse" : "/";
      if (location.pathname !== url) history.pushState({ workspace: next }, "", url);
    }
    if (next === "main") restoreVisibleDiagrams();
    if (prev !== next) {
      document.dispatchEvent(new CustomEvent("tasksense:workspace", { detail: next }));
    }
  }

  function showView(view) {
    document.querySelectorAll("#view-toggle [data-view]").forEach(function (item) {
      item.classList.toggle("on", item.getAttribute("data-view") === view);
    });
    userView.classList.toggle("on", view === "user");
    debugView.classList.toggle("on", view === "debug");
    modalityView.classList.toggle("on", view === "modality");
    try {
      sessionStorage.setItem("tasksense.view", view);
    } catch (err) {}
    restoreVisibleDiagrams();
  }

  function restoreVisibleDiagrams() {
    if (document.body.dataset.workspace === "browse") return;
    requestAnimationFrame(function () {
      const full = fullViewToggle.checked;
      if ((full || debugView.classList.contains("on")) && window.TaskSenseFSM) {
        window.TaskSenseFSM.rerender();
        window.TaskSenseFSM.options("#observe-options", lastStage);
      }
      if (!full && userView.classList.contains("on") && lastPlan) renderPlan(lastPlan);
    });
  }

  function setFullView(enabled) {
    fullViewToggle.checked = enabled;
    document.body.dataset.fullView = String(enabled);
    restoreVisibleDiagrams();
  }

  fullViewToggle.addEventListener("change", function () {
    setFullView(fullViewToggle.checked);
    try {
      sessionStorage.setItem("tasksense.fullView", String(fullViewToggle.checked));
    } catch (err) {}
    window.scrollTo(0, 0);
  });

  document.querySelectorAll("#view-toggle [data-view]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (btn.classList.contains("on")) return;
      showView(btn.getAttribute("data-view"));
      window.scrollTo(0, 0);
    });
  });
  document.querySelectorAll("a[data-workspace], button[data-workspace]").forEach(function (el) {
    el.addEventListener("click", function (evt) {
      const ws = el.getAttribute("data-workspace");
      if (!ws) return;
      if (evt.metaKey || evt.ctrlKey || evt.shiftKey || evt.altKey || evt.button) return;
      evt.preventDefault();
      setWorkspace(ws);
    });
  });
  window.addEventListener("popstate", function () {
    setWorkspace(location.pathname.indexOf("/browse") === 0 ? "browse" : "main", false);
  });
  setWorkspace(document.body.dataset.workspace === "browse" ? "browse" : "main", false);
  try {
    const savedView = sessionStorage.getItem("tasksense.view");
    if (["user", "modality", "debug"].includes(savedView)) showView(savedView);
    setFullView(sessionStorage.getItem("tasksense.fullView") === "true");
  } catch (err) {}

  function appendLog(text) {
    debugLog.textContent += text + "\n";
    debugLog.scrollTop = debugLog.scrollHeight;
  }

  function setStage(payload) {
    lastStage = payload || lastStage;
    const label = (payload && payload.label) || (payload && payload.node) || "运行中";
    const route = payload && payload.route ? " → " + payload.route : "";
    userStage.textContent = label + route;
    fsmCaption.textContent = ((payload && payload.node) || "-") + route;
    if (window.TaskSenseFSM && payload && payload.mermaid) {
      window.TaskSenseFSM.render("#fsm-diagram", payload.mermaid);
      window.TaskSenseFSM.options("#observe-options", payload);
    }
  }

  function ensureUserCard(kind, title) {
    if (userEmpty) userEmpty.remove();
    if (userCards.has(kind)) return userCards.get(kind);
    const card = document.createElement("article");
    card.className = "card user-card-" + kind;
    if (kind === "progress") card.classList.add("full-view-only");
    card.innerHTML = "<h3></h3><div class='card-body'></div>";
    card.querySelector("h3").textContent = title;
    userStream.insertBefore(card, kind === "plan" ? userCards.get("response") || null : null);
    userCards.set(kind, card);
    return card;
  }

  function renderResponse(text) {
    const card = ensureUserCard("response", "Response");
    const body = card.querySelector(".card-body");
    body.classList.add("response");
    window.TaskSenseResponseMarkdown.render(body, text || "");
    window.TaskSenseVoice.attachResponse(card, body);
  }

  function renderPlan(plan) {
    lastPlan = plan;
    const card = ensureUserCard("plan", "Plan");
    const body = card.querySelector(".card-body");
    if (!plan || !plan.length) {
      body.textContent = "未能生成可用计划。";
      return;
    }
    const items = plan
      .map(function (step) {
        const deps = (step.dep || []).map(function (dep) {
          return dep < 0 ? "用户输入" : "#" + dep;
        });
        const args = step.args ? JSON.stringify(step.args) : "";
        return (
          '<div class="plan-item"><div class="plan-id">#' +
          step.id +
          "</div><div><strong>" +
          escapeHtml(step.tool_name || "") +
          '</strong><div class="deps">依赖: ' +
          escapeHtml(deps.join(", ")) +
          (args ? " · " + escapeHtml(args) : "") +
          "</div></div></div>"
        );
      })
      .join("");
    const mermaidId = "plan-dag-" + Date.now();
    const mermaidSrc = planToMermaid(plan);
    body.innerHTML =
      '<div class="plan-list">' +
      items +
      '</div><div class="plan-dag" id="' +
      mermaidId +
      '"></div>';
    if (window.TaskSenseFSM) {
      window.TaskSenseFSM.drawInto(document.getElementById(mermaidId), mermaidSrc);
    }
  }

  function planToMermaid(plan) {
    const lines = ["flowchart TD"];
    lines.push('  start(["用户输入"])');
    plan.forEach(function (step) {
      const id = "n" + step.id;
      const label = String(step.id) + ": " + String(step.tool_name || "").replace(/"/g, "'");
      lines.push("  " + id + '["' + label + '"]');
      (step.dep || [-1]).forEach(function (dep) {
        if (dep < 0) lines.push("  start --> " + id);
        else lines.push("  n" + dep + " --> " + id);
      });
    });
    return lines.join("\n");
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function resetRunUi(query) {
    window.TaskSenseVoice.reset();
    window.TaskSenseModalityFlow.reset();
    userCards.clear();
    lastPlan = null;
    userStream.innerHTML = "";
    ensureUserCard("query", "用户输入").querySelector(".card-body").textContent = query;
    const progress = ensureUserCard("progress", "规划进度").querySelector(".card-body");
    window.TaskSensePlanningProgress.reset(progress);
    debugLog.textContent = "";
    debugState.textContent = "{}";
    setStage({ node: "solvability", label: "可解性检查", mermaid: DEFAULT_FSM });
  }

  function setRunControls(running, cancelling) {
    window.TaskSenseVoice.setRunning(!!running);
    runBtn.disabled = !!running;
    if (stopBtn) stopBtn.disabled = !running || !!cancelling;
  }

  async function startRun(event) {
    event.preventDefault();
    window.TaskSenseVoice.stopInput("语音输入已停止，正在提交文字。");
    const query = queryInput.value.trim();
    if (!query) return;
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    resetRunUi(query);
    window.TaskSenseVoice.beginRun();
    setRunControls(true, false);
    try {
      const res = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query, tick_loop: tickLoop.checked }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
      currentRunId = data.run_id;
      listen(data.run_id);
    } catch (err) {
      appendLog("ERROR " + err.message);
      userStage.textContent = "失败";
      setRunControls(false, false);
      window.TaskSenseModalityFlow.setStatus("查询提交失败，请查看系统调试。");
    }
  }

  async function stopRun() {
    if (!currentRunId || runClosed) return;
    setRunControls(true, true);
    userStage.textContent = "正在终止…";
    try {
      const res = await fetch("/api/run/" + currentRunId + "/stop", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
    } catch (err) {
      appendLog("ERROR " + err.message);
      setRunControls(true, false);
    }
  }

  function listen(runId) {
    runClosed = false;
    currentRunId = runId;
    eventSource = new EventSource("/api/run/" + runId + "/events");
    eventSource.addEventListener("stage", function (evt) {
      setStage(JSON.parse(evt.data));
    });
    eventSource.addEventListener("plan", function (evt) {
      const payload = JSON.parse(evt.data);
      if (!payload.raw) {
        lastPlan = payload.plan || [];
        if (!fullViewToggle.checked) renderPlan(lastPlan);
      }
    });
    eventSource.addEventListener("planning_progress", function (evt) {
      window.TaskSensePlanningProgress.update(JSON.parse(evt.data));
    });
    eventSource.addEventListener("log", function (evt) {
      const payload = JSON.parse(evt.data);
      appendLog(payload.text || "");
    });
    eventSource.addEventListener("state", function (evt) {
      debugState.textContent = JSON.stringify(JSON.parse(evt.data), null, 2);
    });
    eventSource.addEventListener("modality_flow", function (evt) {
      window.TaskSenseModalityFlow.update(JSON.parse(evt.data));
    });
    eventSource.addEventListener("response", function (evt) {
      renderResponse(JSON.parse(evt.data).text);
    });
    eventSource.addEventListener("tick", function (evt) {
      const payload = JSON.parse(evt.data);
      userStage.textContent = "等待下一轮 (" + payload.interval + "s)";
      appendLog("tick-loop sleeping " + payload.interval + "s until " + payload.waiting_until);
    });
    eventSource.addEventListener("cancelling", function () {
      userStage.textContent = "正在终止…";
      setRunControls(true, true);
    });
    eventSource.addEventListener("done", function (evt) {
      const payload = JSON.parse(evt.data);
      if (payload.final_response) renderResponse(payload.final_response);
      window.TaskSenseVoice.completeResponse();
      userStage.textContent = "完成";
      fsmCaption.textContent = "done / " + (payload.route || "");
      appendLog("--- done route=" + (payload.route || "") + " ---");
      finish("本次运行已完成，没有记录到模态工具调用。");
    });
    eventSource.addEventListener("cancelled", function (evt) {
      const payload = JSON.parse(evt.data);
      window.TaskSensePlanningProgress.interrupt("stopped");
      userStage.textContent = "已终止";
      fsmCaption.textContent = "cancelled";
      appendLog("--- cancelled " + (payload.message || "") + " ---");
      finish("本次运行已终止，没有记录到模态工具调用。");
    });
    eventSource.addEventListener("run_error", function (evt) {
      const payload = JSON.parse(evt.data);
      window.TaskSensePlanningProgress.interrupt("error");
      appendLog("ERROR " + (payload.message || evt.data));
      userStage.textContent = "错误";
      finish("本次运行遇到错误，请查看系统调试。");
    });
    eventSource.onerror = function () {
      if (runClosed) return;
      if (eventSource && eventSource.readyState === EventSource.CLOSED) {
        appendLog("事件流已断开");
        finish("运行事件流已断开，刷新页面可重新连接。");
      }
    };
  }

  function finish(modalityStatus) {
    window.TaskSenseModalityFlow.setStatus(modalityStatus);
    if (runClosed && !eventSource) {
      setRunControls(false, false);
      return;
    }
    runClosed = true;
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    setRunControls(false, false);
  }

  async function restoreIfNeeded() {
    try {
      const status = await (await fetch("/api/status")).json();
      if (!status.run_id) return;
      currentRunId = status.run_id;
      if (status.query) queryInput.value = status.query;
      tickLoop.checked = !!status.tick_loop;
      resetRunUi(status.query || queryInput.value || "");
      setRunControls(true, !!status.cancelled);
      listen(status.run_id);
    } catch (err) {}
  }

  form.addEventListener("submit", startRun);
  if (stopBtn) stopBtn.addEventListener("click", stopRun);

  const DEFAULT_FSM = `stateDiagram-v2
    direction LR
    [*] --> solvability
    solvability --> plan: solvable
    solvability --> unsolvable: unsolvable
    unsolvable --> [*]
    plan --> validate
    validate --> plan: retry
    validate --> execute: ok
    validate --> respond: empty
    validate --> bad_plan: fail
    bad_plan --> [*]
    execute --> observe
    observe --> execute: continue
    observe --> plan: replan
    observe --> act: act
    observe --> wait: wait
    observe --> respond: respond
    act --> execute
    wait --> [*]
    respond --> [*]`;

  lastStage = {
    node: "",
    observe_options: ["continue", "replan", "act", "wait", "respond"],
    mermaid: DEFAULT_FSM,
  };

  if (window.TaskSenseFSM) {
    window.TaskSenseFSM.render("#fsm-diagram", DEFAULT_FSM);
    window.TaskSenseFSM.options("#observe-options", lastStage);
  }

  refreshVirtualPill();
  restoreIfNeeded();

  function field(name, label, value, extra) {
    extra = extra || {};
    const type = extra.type || "text";
    const placeholder = extra.placeholder ? ' placeholder="' + extra.placeholder + '"' : "";
    return (
      '<label class="field"><span>' +
      label +
      '</span><input name="' +
      name +
      '" type="' +
      type +
      '" value="' +
      escapeHtml(value == null ? "" : value) +
      '"' +
      placeholder +
      "></label>"
    );
  }

  function textarea(name, label, value) {
    return (
      '<label class="field"><span>' +
      label +
      '</span><textarea name="' +
      name +
      '" rows="4">' +
      escapeHtml(value || "") +
      "</textarea></label>"
    );
  }

  function boolField(name, label, value) {
    return (
      '<label class="check"><input type="checkbox" name="' +
      name +
      '"' +
      (value ? " checked" : "") +
      "> " +
      label +
      "</label>"
    );
  }

  function secretValue(node) {
    if (node && typeof node === "object" && "masked" in node) return node.masked || "";
    return node || "";
  }

  function fillSettings(cfg) {
    document.getElementById("settings-hint").textContent = cfg.restart_hint || "";
    const api = cfg.api_settings || {};
    document.getElementById("pane-api").innerHTML =
      '<div class="grid-2">' +
      field("llm_name", "llm_name", api.llm_name) +
      field("temperature", "temperature", api.temperature, { type: "number" }) +
      field("vllm.base_url", "vllm.base_url", (api.vllm || {}).base_url) +
      field("vllm.model", "vllm.model", (api.vllm || {}).model) +
      field("vllm.temperature", "vllm.temperature", (api.vllm || {}).temperature, { type: "number" }) +
      field("vllm.api_key", "vllm.api_key", "", { type: "password", placeholder: secretValue(api.vllm && api.vllm.api_key) || "留空则不修改" }) +
      field("cohere.api_key", "cohere.api_key", "", { type: "password", placeholder: secretValue(api.cohere && api.cohere.api_key) || "留空则不修改" }) +
      field("azure.api_key", "azure.api_key", "", { type: "password", placeholder: "留空则不修改" }) +
      field("azure.api_version", "azure.api_version", (api.azure || {}).api_version) +
      field("azure.azure_endpoint", "azure.azure_endpoint", (api.azure || {}).azure_endpoint) +
      field("volcengine.api_key", "volcengine.api_key", "", { type: "password", placeholder: "留空则不修改" }) +
      field("volcengine.model", "volcengine.model", (api.volcengine || {}).model) +
      field("poe.api_key", "poe.api_key", "", { type: "password", placeholder: "留空则不修改" }) +
      field("bedrock.api_key", "bedrock.api_key", "", { type: "password", placeholder: "留空则不修改" }) +
      field("bedrock.region_name", "bedrock.region_name", (api.bedrock || {}).region_name) +
      "</div>";

    const graph = cfg.graph_settings || {};
    const policies = graph.observation_policies || {};
    document.getElementById("pane-graph").innerHTML =
      '<div class="grid-2">' +
      field("max_replan", "max_replan", graph.max_replan, { type: "number" }) +
      field("max_validate_retries", "max_validate_retries", graph.max_validate_retries, { type: "number" }) +
      field("tick_interval_seconds", "tick_interval_seconds", graph.tick_interval_seconds, { type: "number" }) +
      field("max_parallel_tools", "max_parallel_tools", graph.max_parallel_tools, { type: "number" }) +
      "</div>" +
      boolField("dry_run_sensor_tools", "dry_run_sensor_tools", graph.dry_run_sensor_tools) +
      boolField("act_requires_monitor_intent", "act_requires_monitor_intent", graph.act_requires_monitor_intent) +
      textarea("monitor_keywords", "monitor_keywords（每行一个）", (graph.monitor_keywords || []).join("\n")) +
      textarea("act_on_labels", "observation_policies.act_on_labels", (policies.act_on_labels || []).join("\n")) +
      textarea("act_tools", "observation_policies.act_tools", (policies.act_tools || []).join("\n"));

    const exec = cfg.execution_settings || {};
    document.getElementById("pane-exec").innerHTML =
      field("dataset_name", "dataset_name", cfg.dataset_name) +
      field("device", "device", exec.device) +
      boolField("use_cache", "use_cache", exec.use_cache) +
      boolField("use_dynamic_plan_adaptation", "按小时自动选择 RGB / Depth", exec.use_dynamic_plan_adaptation) +
      boolField("measure_flops", "measure_flops", exec.measure_flops);

    const virtual = cfg.llm_virtual_env || {};
    const virtualApi = virtual.api || {};
    document.getElementById("pane-virtual").innerHTML =
      boolField("virtual.enabled", "进入 LLM 虚拟环境模式", virtual.enabled) +
      '<p class="hint">开启后，传感器查询与 HAR 等工具不再读取真实文件或跑模型，改由下方专用 LLM 生成符合格式的调用结果，并使用独立 cache。退出虚拟模式时不会清空 cache；重新生成 vocabulary / grammar 后会自动清空。也可在下方手动清空。</p>' +
      '<div class="grid-2">' +
      field("virtual.base_url", "虚拟环境 base_url", virtualApi.base_url) +
      field("virtual.model", "虚拟环境 model", virtualApi.model) +
      field("virtual.temperature", "虚拟环境 temperature", virtualApi.temperature, { type: "number" }) +
      field("virtual.api_key", "虚拟环境 api_key", "", {
        type: "password",
        placeholder: secretValue(virtualApi.api_key) || "留空则不修改",
      }) +
      "</div>" +
      '<div class="virtual-cache-actions">' +
      '<button type="button" class="btn danger" id="btn-clear-virtual-cache">手动清空 cache</button>' +
      '<p class="hint">仅清空 LLM 虚拟环境的独立调用 cache，不影响真实执行 cache 与已生成的 grammar。</p>' +
      "</div>";
    setVirtualPill(!!virtual.enabled);
    const clearBtn = document.getElementById("btn-clear-virtual-cache");
    if (clearBtn) clearBtn.addEventListener("click", clearVirtualCache);
  }

  async function clearVirtualCache() {
    const status = document.getElementById("settings-status");
    if (!window.confirm("确定清空 LLM 虚拟环境 cache 吗？")) return;
    try {
      const res = await fetch("/api/llm-virtual-env/cache/clear", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "清空失败");
      status.className = "status";
      status.textContent = "已手动清空 LLM 虚拟环境 cache（" + ((data.cache && data.cache.total) || 0) + " 条）";
    } catch (err) {
      status.className = "status err";
      status.textContent = err.message;
    }
  }

  function readSettings() {
    const formEl = document.getElementById("settings-form");
    const get = function (name) {
      const el = formEl.querySelector('[name="' + name + '"]');
      return el ? el.value : "";
    };
    const checked = function (name) {
      const el = formEl.querySelector('[name="' + name + '"]');
      return !!(el && el.checked);
    };
    return {
      api_settings: {
        llm_name: get("llm_name"),
        temperature: Number(get("temperature")),
        vllm: {
          base_url: get("vllm.base_url"),
          model: get("vllm.model"),
          temperature: Number(get("vllm.temperature")),
          api_key: get("vllm.api_key"),
        },
        cohere: { api_key: get("cohere.api_key") },
        azure: {
          api_key: get("azure.api_key"),
          api_version: get("azure.api_version"),
          azure_endpoint: get("azure.azure_endpoint"),
        },
        volcengine: { api_key: get("volcengine.api_key"), model: get("volcengine.model") },
        poe: { api_key: get("poe.api_key") },
        bedrock: { api_key: get("bedrock.api_key"), region_name: get("bedrock.region_name") },
      },
      dataset_name: get("dataset_name"),
      graph_settings: {
        max_replan: Number(get("max_replan")),
        max_validate_retries: Number(get("max_validate_retries")),
        tick_interval_seconds: Number(get("tick_interval_seconds")),
        max_parallel_tools: Number(get("max_parallel_tools")),
        dry_run_sensor_tools: checked("dry_run_sensor_tools"),
        act_requires_monitor_intent: checked("act_requires_monitor_intent"),
        monitor_keywords: get("monitor_keywords"),
        observation_policies: {
          act_on_labels: get("act_on_labels"),
          act_tools: get("act_tools"),
        },
      },
      execution_settings: {
        device: get("device"),
        use_cache: checked("use_cache"),
        use_dynamic_plan_adaptation: checked("use_dynamic_plan_adaptation"),
        measure_flops: checked("measure_flops"),
      },
      llm_virtual_env: {
        enabled: checked("virtual.enabled"),
        api: {
          base_url: get("virtual.base_url"),
          model: get("virtual.model"),
          temperature: Number(get("virtual.temperature")),
          api_key: get("virtual.api_key"),
        },
      },
    };
  }

  function setVirtualPill(enabled) {
    const pill = document.getElementById("virtual-pill");
    if (!pill) return;
    pill.hidden = !enabled;
  }

  async function refreshVirtualPill() {
    try {
      const res = await fetch("/api/config");
      const cfg = await res.json();
      setVirtualPill(!!(cfg.llm_virtual_env && cfg.llm_virtual_env.enabled));
    } catch (err) {}
  }

  document.getElementById("btn-settings").addEventListener("click", async function () {
    const status = document.getElementById("settings-status");
    status.textContent = "";
    status.className = "status";
    const res = await fetch("/api/config");
    fillSettings(await res.json());
    settingsModal.showModal();
  });
  document.getElementById("btn-close-settings").addEventListener("click", function () {
    settingsModal.close();
  });

  document.querySelectorAll("#settings-tabs .tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll("#settings-tabs .tab").forEach(function (item) {
        item.classList.toggle("on", item === tab);
      });
      const pane = tab.getAttribute("data-pane");
      document.querySelectorAll(".modal-card .pane").forEach(function (item) {
        item.classList.toggle("on", item.getAttribute("data-pane") === pane);
      });
    });
  });

  document.getElementById("btn-save-settings").addEventListener("click", async function () {
    const status = document.getElementById("settings-status");
    try {
      const res = await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(readSettings()),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "保存失败");
      status.className = "status";
      status.textContent =
        "已保存" +
        (data.restart_needed && data.restart_needed.length
          ? "。需重启: " + data.restart_needed.join(", ")
          : "");
      fillSettings(data.config);
    } catch (err) {
      status.className = "status err";
      status.textContent = err.message;
    }
  });
})();
