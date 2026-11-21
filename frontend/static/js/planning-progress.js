(function () {
  const stages = {
    solvability: { title: "可解性检查", passed: "请求可处理", failed: "请求无法处理", skipped: "跳过检查", uncertain: "结果未明确，继续规划" },
    plan: { title: "生成计划", passed: "已生成" },
    validate: { title: "校验计划", retry: "未通过，重新规划", empty: "未生成工具调用" },
  };
  const statuses = {
    waiting: "未开始", running: "进行中", passed: "已通过", failed: "未通过",
    stopped: "已中止", error: "发生错误",
  };
  const checks = {
    model_error: { passed: "规划结果未包含错误标记", failed: "规划结果包含错误标记" },
    json: { passed: "JSON 格式有效", failed: "JSON 格式无效" },
    nonempty: { passed: "计划包含工具调用", empty: "计划没有工具调用" },
    tool_list: { failed: "计划应为工具调用列表" },
    dependency_ids: { failed: "工具编号或依赖编号格式有误" },
    grammar: { passed: "工具和依赖符合编排规则", failed: "工具或依赖不符合编排规则" },
  };
  const toolTypes = {
    "query-video-data": "查询视频", "query-audio-data": "查询音频", "object-detection": "目标检测",
    "human-activity-classification": "行为分类", "scene-description": "场景描述",
  };
  const modalities = { rgb: "RGB", depth: "Depth", audio: "音频" };
  const fieldNames = { args: "参数", dep: "依赖", id: "编号", tool_name: "名称" };
  const icons = { passed: "✓", failed: "×", retry: "↻", empty: "–" };
  let body, states, attemptKey, attemptLabel, items, plan, announcement;
  let renderQueued = false;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function toolLabel(name) {
    const type = Object.keys(toolTypes).find((prefix) => name === prefix || name?.startsWith(prefix + "-"));
    if (!type) return name || "工具";
    const modality = name.slice(type.length + 1);
    return toolTypes[type] + (modality ? " · " + (modalities[modality] || modality) : "");
  }

  function checkLabel(event) {
    if (event.check === "tool_fields") {
      const label = toolLabel(event.tool) + " · ";
      return label + (event.status === "passed" ? "必填字段齐全" : "缺少" + fieldNames[event.missing]);
    }
    return checks[event.check]?.[event.status];
  }

  function toolFlow() {
    const section = element("section", "planning-tool-flow");
    section.append(element("h4", "", "工具调用关系"));
    const steps = element("ol", "planning-tools");
    const names = new Map(plan.map((step) => [step.id, toolLabel(step.tool_name)]));
    for (const step of plan) {
      const row = element("li", "planning-tool");
      row.append(element("strong", "", "#" + step.id + " " + names.get(step.id)));
      const inputs = element("div", "planning-inputs");
      for (const dep of step.dep?.length ? step.dep : [-1]) {
        const label = dep < 0 ? "用户输入" : "#" + dep + (names.has(dep) ? " " + names.get(dep) : "");
        inputs.append(element("span", "planning-input", "← " + label));
      }
      row.append(inputs);
      if (step.args && Object.keys(step.args).length) {
        const params = element("details", "planning-args");
        params.append(element("summary", "", "调用参数"), element("pre", "", JSON.stringify(step.args, null, 2)));
        row.append(params);
      }
      steps.append(row);
    }
    section.append(steps);
    return section;
  }

  function render() {
    const phases = element("ol", "planning-stages");
    for (const [key, stage] of Object.entries(stages)) {
      const status = states[key] || "waiting";
      const row = element("li", "planning-stage planning-" + status);
      row.append(element("strong", "", stage.title), element("span", "planning-stage-status", stage[status] || statuses[status]));
      phases.append(row);
    }
    const content = [phases];
    if (attemptLabel) content.push(element("p", "muted planning-attempt", attemptLabel));
    if (items.size) {
      const rows = element("ul", "planning-checks");
      for (const item of items.values()) {
        const row = element("li", "planning-" + item.status);
        const icon = element("span", "planning-check-icon", icons[item.status]);
        icon.setAttribute("aria-hidden", "true");
        row.append(icon, element("span", "", item.label));
        rows.append(row);
      }
      content.push(rows);
    }
    if (plan.length) content.push(toolFlow());
    body.replaceChildren(...content);
  }

  function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(function () {
      renderQueued = false;
      if (body) render();
    });
  }

  window.TaskSensePlanningProgress = {
    reset(container) {
      body = container;
      states = {};
      items = new Map();
      attemptKey = null;
      attemptLabel = "";
      plan = [];
      announcement = document.getElementById("planning-announcement");
      announcement.textContent = "";
      scheduleRender();
    },
    update(event) {
      if (!body || !stages[event.stage]) return;
      const key = event.round + "/" + event.retry;
      if (event.stage !== "solvability" && key !== attemptKey) {
        attemptKey = key;
        attemptLabel = event.round || event.retry ? `第 ${event.round + 1} 轮 · 第 ${event.retry + 1} 次尝试` : "";
        delete states.plan;
        delete states.validate;
        items.clear();
        plan = [];
      }
      if (event.check) {
        const label = checkLabel(event);
        if (!label) return;
        items.set(event.check + "/" + (event.index ?? ""), { label, status: event.status });
        announcement.textContent = label;
      } else {
        states[event.stage] = event.status;
        if (event.stage === "validate" && event.status === "passed") plan = event.plan || [];
        const stage = stages[event.stage];
        announcement.textContent = stage.title + " · " + (stage[event.status] || statuses[event.status]);
      }
      scheduleRender();
    },
    interrupt(status) {
      if (!body) return;
      const stage = Object.keys(stages).find((key) => states[key] === "running");
      if (!stage) return;
      states[stage] = status;
      announcement.textContent = stages[stage].title + " · " + statuses[status];
      scheduleRender();
    },
  };
})();
