(function () {
  const query = document.getElementById("query-input");
  const inputButton = document.getElementById("btn-voice-input");
  const language = document.getElementById("voice-language");
  const inputStatus = document.getElementById("voice-input-status");
  const autoRead = document.getElementById("voice-auto-read");
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synthesis = window.speechSynthesis;
  const canSpeak = !!(synthesis && window.SpeechSynthesisUtterance);
  const canRecognize = !!Recognition && window.isSecureContext;
  const inputErrors = {
    "not-allowed": "未获得麦克风权限，请在浏览器的站点设置中允许使用麦克风后重试。",
    "service-not-allowed": "浏览器的语音识别服务不可用，请换用支持语音识别的浏览器。",
    "audio-capture": "无法访问麦克风，请检查麦克风是否已连接或被其他应用占用。",
    "no-speech": "没有检测到语音，请重新点击语音输入。",
    network: "语音识别服务连接失败，请检查网络后重试。",
    "language-not-supported": "语音识别服务不支持所选语言，请切换语言后重试。",
    aborted: "语音输入已停止。",
  };
  let recognition = null;
  let taskRunning = false;
  let readNewResponse = false;
  let responseText = "";
  let controls = null;
  let activeSpeech = null;
  let speechPaused = false;
  let chunks = [];
  let chunkIndex = 0;
  let waitingForNextChunk = false;
  let speechLanguage = "zh-CN";
  let speechGeneration = 0;

  try {
    const savedLanguage = localStorage.getItem("sensoragent.voice.language");
    if (["zh-CN", "en-US"].includes(savedLanguage)) language.value = savedLanguage;
    autoRead.checked = localStorage.getItem("sensoragent.voice.autoRead") === "true";
  } catch (err) {}

  function updateInputButton() {
    inputButton.disabled = !canRecognize || taskRunning;
    inputButton.textContent = recognition ? "停止语音输入" : "语音输入";
    inputButton.setAttribute("aria-pressed", String(!!recognition));
    language.disabled = !!recognition;
  }

  function stopInput(message) {
    const previous = recognition;
    recognition = null;
    if (previous) {
      try { previous.abort(); } catch (err) {}
      if (message) inputStatus.textContent = message;
    }
    updateInputButton();
    updateSpeechControls();
  }

  function updateSpeechControls(message) {
    if (!controls) return;
    controls.play.disabled = !canSpeak || !responseText || !!recognition;
    controls.play.textContent = activeSpeech ? "重新朗读" : "朗读回复";
    controls.pause.disabled = !activeSpeech;
    controls.pause.textContent = speechPaused ? "继续朗读" : "暂停";
    controls.stop.disabled = !activeSpeech;
    if (message !== undefined) controls.status.textContent = message;
  }

  function stopSpeech(message) {
    speechGeneration += 1;
    activeSpeech = null;
    speechPaused = false;
    waitingForNextChunk = false;
    if (canSpeak) {
      synthesis.cancel();
      if (synthesis.paused) synthesis.resume();
    }
    updateSpeechControls(message);
  }

  function startInput() {
    if (!canRecognize || taskRunning) return;
    stopSpeech("");
    const before = query.value.slice(0, query.selectionStart);
    const after = query.value.slice(query.selectionEnd);
    let session;
    let heard = false;
    try {
      session = new Recognition();
      session.lang = language.value;
      session.continuous = true;
      session.interimResults = true;
      session.maxAlternatives = 1;
      recognition = session;
      session.onstart = function () {
        if (recognition !== session) return;
        inputStatus.textContent = "正在聆听，识别文字会填入输入框。";
      };
      session.onresult = function (event) {
        if (recognition !== session) return;
        const parts = [];
        let interim = false;
        for (let index = 0; index < event.results.length; index++) {
          parts.push(event.results[index][0].transcript);
          interim ||= !event.results[index].isFinal;
        }
        const transcript = parts.join(session.lang === "en-US" ? " " : "");
        if (!transcript) return;
        heard = true;
        const separator = before && !/\s$/.test(before) ? " " : "";
        const tail = after && !/^\s/.test(after) ? " " : "";
        const text = separator + transcript + tail;
        query.value = before + text + after;
        const caret = before.length + text.length;
        query.setSelectionRange(caret, caret);
        inputStatus.textContent = interim ? "正在识别…" : "已识别，继续说话或点击停止。";
      };
      session.onerror = function (event) {
        if (recognition !== session) return;
        stopInput();
        inputStatus.textContent = inputErrors[event.error] || "语音识别失败，请重试。";
      };
      session.onend = function () {
        if (recognition !== session) return;
        recognition = null;
        updateInputButton();
        updateSpeechControls();
        inputStatus.textContent = heard ? "语音输入已结束，可编辑文字后提交。" : "未识别到文字，请重试。";
      };
      inputStatus.textContent = "正在启动麦克风，请允许浏览器使用麦克风。";
      updateInputButton();
      updateSpeechControls();
      session.start();
    } catch (err) {
      stopInput();
      inputStatus.textContent = "无法启动语音识别，请检查浏览器支持情况及麦克风权限。";
    }
  }

  function speechChunks(text) {
    const result = [];
    let remaining = text.trim();
    while (remaining.length > 180) {
      const prefix = remaining.slice(0, 180);
      const boundaries = [...prefix.matchAll(/[。！？.!?;；\n\s]/g)];
      const last = boundaries.at(-1)?.index;
      const end = last >= 60 ? last + 1 : 180;
      result.push(remaining.slice(0, end));
      remaining = remaining.slice(end).trimStart();
    }
    if (remaining) result.push(remaining);
    return result;
  }

  function speakNext(generation) {
    if (generation !== speechGeneration) return;
    waitingForNextChunk = false;
    if (chunkIndex >= chunks.length) {
      activeSpeech = null;
      speechPaused = false;
      updateSpeechControls("朗读完成。");
      return;
    }
    const utterance = new window.SpeechSynthesisUtterance(chunks[chunkIndex]);
    utterance.lang = speechLanguage;
    const voices = synthesis.getVoices();
    const voice = voices.find((item) => item.lang === speechLanguage) ||
      voices.find((item) => item.lang.split("-")[0] === speechLanguage.split("-")[0]);
    if (voice) utterance.voice = voice;
    activeSpeech = utterance;
    utterance.onend = function () {
      if (generation !== speechGeneration) return;
      chunkIndex += 1;
      if (speechPaused) waitingForNextChunk = true;
      else speakNext(generation);
    };
    utterance.onerror = function (event) {
      if (generation !== speechGeneration) return;
      stopSpeech(event.error === "not-allowed"
        ? "浏览器未允许自动播放，请点击朗读回复。"
        : "朗读失败，请检查系统语音包或重新点击朗读回复。");
    };
    try {
      synthesis.speak(utterance);
      updateSpeechControls("正在朗读…");
    } catch (err) {
      stopSpeech("无法启动朗读，请检查浏览器语音支持。");
    }
  }

  function playResponse() {
    if (!canSpeak || !responseText) return;
    stopInput("语音输入已停止。");
    stopSpeech("");
    speechLanguage = /[\u3400-\u9fff]/.test(responseText) ? "zh-CN" : "en-US";
    chunks = speechChunks(responseText);
    chunkIndex = 0;
    speakNext(speechGeneration);
  }

  function button(text, callback) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "btn";
    node.textContent = text;
    node.addEventListener("click", callback);
    return node;
  }

  function attachResponse(card, body) {
    const text = (body.innerText || body.textContent || "").trim();
    if (text !== responseText) {
      stopSpeech("");
      responseText = text;
    }
    if (!controls || !card.contains(controls.host)) {
      const host = document.createElement("div");
      host.className = "voice-output-controls";
      const status = document.createElement("span");
      status.className = "voice-status";
      status.setAttribute("role", "status");
      const play = button("朗读回复", playResponse);
      const pause = button("暂停", function () {
        if (!activeSpeech) return;
        speechPaused = !speechPaused;
        if (speechPaused) synthesis.pause();
        else {
          synthesis.resume();
          if (waitingForNextChunk) speakNext(speechGeneration);
        }
        updateSpeechControls(speechPaused ? "已暂停。" : "正在朗读…");
      });
      const stop = button("停止朗读", () => stopSpeech("朗读已停止。"));
      host.append(play, pause, stop, status);
      card.append(host);
      controls = { host, play, pause, stop, status };
    }
    updateSpeechControls(canSpeak ? undefined : "当前浏览器不支持语音朗读。");
  }

  inputButton.addEventListener("click", function () {
    if (recognition) {
      stopInput("语音输入已停止，可编辑文字后提交。");
    } else startInput();
  });
  query.addEventListener("input", function () {
    stopInput("语音输入已停止，已保留你编辑的文字。");
  });
  language.addEventListener("change", function () {
    try { localStorage.setItem("sensoragent.voice.language", language.value); } catch (err) {}
  });
  autoRead.disabled = !canSpeak;
  autoRead.addEventListener("change", function () {
    try { localStorage.setItem("sensoragent.voice.autoRead", String(autoRead.checked)); } catch (err) {}
  });
  document.addEventListener("tasksense:workspace", function (event) {
    if (event.detail === "browse") {
      stopInput("语音输入已停止。");
      stopSpeech("");
    }
  });
  window.addEventListener("pagehide", function () {
    stopInput();
    stopSpeech();
  });

  if (!window.isSecureContext) inputStatus.textContent = "语音输入需要 HTTPS 或本机 localhost 地址。";
  else if (!Recognition) inputStatus.textContent = "当前浏览器不支持语音输入，请在 Chrome 或 Edge 中打开本页。";
  else inputStatus.textContent = "点击语音输入开始，首次使用需要允许麦克风权限。";
  updateInputButton();

  window.TaskSenseVoice = {
    stopInput,
    attachResponse,
    reset() {
      stopInput("语音输入已停止。");
      stopSpeech("");
      controls = null;
      responseText = "";
      readNewResponse = false;
    },
    beginRun() { readNewResponse = true; },
    setRunning(running) {
      taskRunning = running;
      if (running) stopInput("语音输入已停止，正在处理请求。");
      updateInputButton();
    },
    completeResponse() {
      const shouldRead = readNewResponse && autoRead.checked && !recognition;
      readNewResponse = false;
      if (shouldRead) playResponse();
    },
  };
})();
