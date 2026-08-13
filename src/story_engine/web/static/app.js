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
  retryDeliveryButton: document.getElementById("retryDeliveryButton"),
  awareness: document.getElementById("awareness"),
  awarenessLocation: document.getElementById("awarenessLocation"),
  awarenessObservations: document.getElementById("awarenessObservations"),
  visibleActorsGroup: document.getElementById("visibleActorsGroup"),
  visibleActors: document.getElementById("visibleActors"),
  visibleObjectsGroup: document.getElementById("visibleObjectsGroup"),
  visibleObjects: document.getElementById("visibleObjects"),
  activeGoalsGroup: document.getElementById("activeGoalsGroup"),
  activeGoals: document.getElementById("activeGoals"),
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
  { key: "input", label: "提交动作", target: 18 },
  { key: "simulation", label: "推进事件并结算", target: 52 },
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
  const playerReady = !state.data || !state.data.player || state.data.player.ready !== false;
  const deliveryReady = !state.data || state.data.delivery_pending !== true;
  els.submitButton.disabled = flag || !playerReady || !deliveryReady;
  els.commandInput.disabled = flag || !playerReady || !deliveryReady;
  els.autoButton.disabled = flag || !deliveryReady;
  els.retryDeliveryButton.disabled = flag || deliveryReady;
  els.resetButton.disabled = flag;
  if (typeof message === "string") {
    els.statusText.textContent = !flag && !deliveryReady
      ? "世界已经提交，但文本/记忆交付尚未完成；请先重试交付。"
      : !flag && !playerReady
      ? "你的行动尚未完成，可以让世界推进到下一个事件。"
      : message;
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
    fragment.querySelector(".entry__kind").textContent = entry.kind === "prologue"
      ? "Prologue"
      : entry.kind === "system" ? "System" : "Turn";
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

function renderTextList(group, list, values) {
  list.innerHTML = "";
  const normalized = (values || [])
    .map((value) => String(value || "").trim())
    .filter(Boolean);
  group.hidden = normalized.length === 0;
  normalized.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    list.appendChild(item);
  });
  return normalized.length;
}

function renderDecisionContext(player) {
  const context = player && player.decision_context ? player.decision_context : {};
  const pendingIds = new Set([
    ...(context.pending_world_events || []),
    ...(context.pending_event_responses || []),
  ]);
  const observations = (context.passive_observations || [])
    .filter((item) => item && (
      pendingIds.has(item.event_id) || pendingIds.has(item.response_id)
    ))
    .map((item) => item.result ? String(item.result).trim() : "")
    .filter(Boolean)
    .slice(-4);
  const representedPending = observations.length;
  (context.active_observation_results || [])
    .map((item) => item && (item.private_result || item.result)
      ? String(item.private_result || item.result).trim()
      : "")
    .filter(Boolean)
    .slice(-2)
    .forEach((value) => observations.push(value));
  const pendingCount = (context.pending_world_events || []).length
    + (context.pending_event_responses || []).length;
  if (pendingCount > representedPending) {
    observations.push(`另有 ${pendingCount - representedPending} 项变化等待处理`);
  }
  els.awarenessObservations.innerHTML = "";
  observations.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    els.awarenessObservations.appendChild(item);
  });

  const actorCount = renderTextList(
    els.visibleActorsGroup,
    els.visibleActors,
    (context.visible_actors || []).slice(0, 6),
  );
  const objectCount = renderTextList(
    els.visibleObjectsGroup,
    els.visibleObjects,
    (context.visible_objects || []).slice(0, 6),
  );
  const goalCount = renderTextList(
    els.activeGoalsGroup,
    els.activeGoals,
    (context.active_goals || [])
      .map((goal) => goal && goal.title ? goal.title : "")
      .slice(0, 4),
  );
  els.awarenessLocation.textContent = context.location
    ? `位于 ${context.location}`
    : "";
  els.awareness.hidden = !(
    context.location
    || observations.length
    || actorCount
    || objectCount
    || goalCount
  );
}

function renderState(data) {
  state.data = data;
  els.title.textContent = data.title;
  els.subtitle.textContent = data.scenario.description;
  els.stepCount.textContent = data.step_count;
  const player = data && data.player ? data.player : null;
  els.playerLine.textContent = player && player.name
    ? `你当前扮演 ${player.name}${player.role ? ` · ${player.role}` : ""}${player.ready === false ? " · 行动进行中" : ""}`
    : "当前没有指定玩家角色。";
  els.leadText.textContent = data.last_step
    ? "界面只保留叙事和你的行动记录，不直接展示后台状态机、storylet 或导演信息。"
    : "这里展示的是玩家视角下的开场文本，而不是世界引擎的内部数据。";
  renderDecisionContext(player);

  renderTranscript(data.history || []);
  els.retryDeliveryButton.hidden = data.delivery_pending !== true;
  els.statusText.textContent = data.delivery_pending === true
    ? "世界已经提交，但文本/记忆交付尚未完成；请先重试交付。"
    : player && player.ready === false
    ? "你的行动尚未完成，可以让世界推进到下一个事件。"
    : "准备就绪。";
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

async function retryDelivery() {
  setBusy(true, "正在重试文本与记忆交付...");
  startExecutionProgress();
  let succeeded = false;
  try {
    const data = await request("/api/retry-delivery", {
      method: "POST",
      body: "{}",
    });
    renderState(data);
    succeeded = data.delivery_pending !== true;
  } catch (error) {
    els.statusText.textContent = `交付重试失败：${error.message}`;
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

els.retryDeliveryButton.addEventListener("click", () => {
  retryDelivery();
});

els.commandInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    submitTurn(els.commandInput.value, els.injectInput.value);
  }
});

loadState();
