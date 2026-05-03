const state = {
  data: null,
  busy: false,
  progressTimer: null,
  progressFrame: null,
  progressStartedAt: 0,
  progressValue: 0,
  progressStageIndex: -1,
};

const els = {
  title: document.getElementById("title"),
  subtitle: document.getElementById("subtitle"),
  playerLine: document.getElementById("playerLine"),
  leadText: document.getElementById("leadText"),
  stepCount: document.getElementById("stepCount"),
  transcript: document.getElementById("transcript"),
  commandInput: document.getElementById("commandInput"),
  injectInput: document.getElementById("injectInput"),
  submitButton: document.getElementById("submitButton"),
  autoButton: document.getElementById("autoButton"),
  resetButton: document.getElementById("resetButton"),
  statusText: document.getElementById("statusText"),
  progressBox: document.getElementById("progressBox"),
  progressLabel: document.getElementById("progressLabel"),
  progressPercent: document.getElementById("progressPercent"),
  progressFill: document.getElementById("progressFill"),
  entryTemplate: document.getElementById("entryTemplate"),
};
els.progressStages = Array.from(document.querySelectorAll(".progress-stage"));

const executionStages = [
  { key: "input", label: "收集输入", target: 18 },
  { key: "simulation", label: "结算裁决", target: 52 },
  { key: "rendering", label: "生成文本", target: 82 },
  { key: "memory", label: "收束本轮", target: 94 },
];
const executionStageThresholds = [22, 56, 88, 96];

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return response.json();
}

function setBusy(flag, message) {
  state.busy = flag;
  els.submitButton.disabled = flag;
  els.autoButton.disabled = flag;
  els.resetButton.disabled = flag;
  if (typeof message === "string") {
    els.statusText.textContent = message;
  }
}

function updateProgressUI(progress, activeIndex, label) {
  const clamped = Math.max(0, Math.min(100, Math.round(progress)));
  els.progressFill.style.width = `${clamped}%`;
  els.progressPercent.textContent = `${clamped}%`;
  els.progressLabel.textContent = label;
  els.progressBox.classList.add("is-visible");
  els.progressBox.setAttribute("aria-hidden", "false");
  const progressBar = els.progressBox.querySelector(".progress-bar");
  if (progressBar) {
    progressBar.setAttribute("aria-valuenow", String(clamped));
  }

  els.progressStages.forEach((stageEl, index) => {
    stageEl.classList.toggle("is-active", index === activeIndex);
    stageEl.classList.toggle("is-done", index < activeIndex);
  });
}

function hideProgressUI() {
  els.progressBox.classList.remove("is-visible");
  els.progressBox.setAttribute("aria-hidden", "true");
  els.progressFill.style.width = "0%";
  els.progressPercent.textContent = "0%";
  els.progressLabel.textContent = "本轮执行中";
  els.progressStages.forEach((stageEl) => {
    stageEl.classList.remove("is-active", "is-done");
  });
}

function startExecutionProgress() {
  stopExecutionProgress(false);
  state.progressValue = 6;
  state.progressStageIndex = 0;
  state.progressStartedAt = Date.now();
  updateProgressUI(state.progressValue, state.progressStageIndex, executionStages[0].label);

  const tick = () => {
    const elapsed = Date.now() - state.progressStartedAt;
    const easedProgress = 96 * (1 - Math.exp(-elapsed / 1050));
    state.progressValue = Math.max(state.progressValue, Math.min(96, easedProgress));

    let activeIndex = executionStages.length - 1;
    for (let index = 0; index < executionStageThresholds.length; index += 1) {
      if (state.progressValue < executionStageThresholds[index]) {
        activeIndex = index;
        break;
      }
    }
    state.progressStageIndex = activeIndex;

    let label = executionStages[activeIndex].label;
    if (activeIndex === executionStages.length - 1 && elapsed > 2400) {
      label = "等待结果返回";
    }

    updateProgressUI(state.progressValue, activeIndex, label);
    state.progressFrame = window.requestAnimationFrame(tick);
  };

  state.progressFrame = window.requestAnimationFrame(tick);
}

function stopExecutionProgress(markDone = true) {
  if (state.progressTimer) {
    window.clearInterval(state.progressTimer);
    state.progressTimer = null;
  }
  if (state.progressFrame) {
    window.cancelAnimationFrame(state.progressFrame);
    state.progressFrame = null;
  }

  if (!markDone) {
    hideProgressUI();
    return;
  }

  updateProgressUI(100, executionStages.length - 1, "本轮完成");
  window.setTimeout(() => {
    if (!state.busy) {
      hideProgressUI();
    }
  }, 240);
}

function renderTranscript(history) {
  els.transcript.innerHTML = "";

  history.forEach((entry) => {
    const fragment = els.entryTemplate.content.cloneNode(true);
    const root = fragment.querySelector(".entry");
    fragment.querySelector(".entry__step").textContent = entry.step != null ? entry.step : 0;
    fragment.querySelector(".entry__kind").textContent = entry.kind === "prologue" ? "Prologue" : "Turn";
    fragment.querySelector(".entry__title").textContent = entry.title || "未命名片段";
    fragment.querySelector(".entry__command").textContent = entry.player_command
      ? `你的行动：${entry.player_command}`
      : "";
    fragment.querySelector(".entry__injected").textContent = entry.inject_event
      ? `世界异动：${entry.inject_event}`
      : "";
    fragment.querySelector(".entry__narration").textContent = entry.narration || "";

    if (!fragment.querySelector(".entry__command").textContent) {
      fragment.querySelector(".entry__command").remove();
    }
    if (!fragment.querySelector(".entry__injected").textContent) {
      fragment.querySelector(".entry__injected").remove();
    }

    els.transcript.appendChild(root);
  });

  els.transcript.scrollTop = els.transcript.scrollHeight;
}

function renderState(data) {
  state.data = data;
  els.title.textContent = data.title;
  els.subtitle.textContent = data.scenario.description;
  els.stepCount.textContent = data.step_count;
  const player = data && data.player ? data.player : null;
  els.playerLine.textContent = player && player.name
    ? `你当前扮演 ${player.name}${player.role ? ` · ${player.role}` : ""}`
    : "当前没有指定玩家角色。";
  els.leadText.textContent = data.last_step
    ? "界面只保留叙事和你的行动记录，不直接展示后台状态机、storylet 或导演信息。"
    : "这里展示的是玩家视角下的开场文本，而不是世界引擎的内部数据。";

  renderTranscript(data.history || []);
  els.statusText.textContent = "准备就绪。";
  if (!state.busy) {
    hideProgressUI();
  }
}

async function loadState() {
  setBusy(true, "正在载入世界...");
  try {
    const data = await request("/api/state");
    renderState(data);
  } catch (error) {
    els.statusText.textContent = `载入失败：${error.message}`;
  } finally {
    setBusy(false, "准备就绪。");
  }
}

async function submitTurn(command = "", injectEvent = "") {
  setBusy(true, "正在执行一步...");
  startExecutionProgress();
  let succeeded = false;
  try {
    const data = await request("/api/step", {
      method: "POST",
      body: JSON.stringify({
        command,
        inject_event: injectEvent,
      }),
    });
    renderState(data);
    els.commandInput.value = "";
    els.injectInput.value = "";
    succeeded = true;
  } catch (error) {
    els.statusText.textContent = `执行失败：${error.message}`;
  } finally {
    setBusy(false, succeeded ? "准备就绪。" : undefined);
    stopExecutionProgress(succeeded);
  }
}

async function resetGame() {
  setBusy(true, "正在重置...");
  startExecutionProgress();
  let succeeded = false;
  try {
    const data = await request("/api/reset", {
      method: "POST",
      body: "{}",
    });
    renderState(data);
    succeeded = true;
  } catch (error) {
    els.statusText.textContent = `重置失败：${error.message}`;
  } finally {
    setBusy(false, succeeded ? "准备就绪。" : undefined);
    stopExecutionProgress(succeeded);
  }
}

els.submitButton.addEventListener("click", () => {
  submitTurn(els.commandInput.value, els.injectInput.value);
});

els.autoButton.addEventListener("click", () => {
  submitTurn("", els.injectInput.value);
});

els.resetButton.addEventListener("click", () => {
  resetGame();
});

els.commandInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    submitTurn(els.commandInput.value, els.injectInput.value);
  }
});

loadState();
