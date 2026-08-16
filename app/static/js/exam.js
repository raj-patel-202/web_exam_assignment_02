(() => {
  "use strict";

  const questions = JSON.parse(document.querySelector("#questions-data").textContent);
  const config = JSON.parse(document.querySelector("#exam-config").textContent);
  const card = document.querySelector("#question-card");
  const palette = document.querySelector("#question-palette");
  const counter = document.querySelector("#question-counter");
  const saveStatus = document.querySelector("#save-status");
  const answeredCount = document.querySelector("#answered-count");
  const unansweredCount = document.querySelector("#unanswered-count");
  const timer = document.querySelector("#exam-timer strong");
  const previousButton = document.querySelector("#previous-question");
  const nextButton = document.querySelector("#next-question");
  const submitButton = document.querySelector("#submit-exam");
  const fullscreenGate = document.querySelector("#fullscreen-gate");
  const fullscreenGateButton = document.querySelector("#fullscreen-gate-button");
  const fullscreenGateTitle = document.querySelector("#fullscreen-gate-title");
  const fullscreenGateCopy = document.querySelector("#fullscreen-gate-copy");
  const workspace = document.querySelector("#exam-workspace");
  const notice = document.querySelector("#exam-notice");
  const submitConfirmation = document.querySelector("#exam-submit-confirm");
  const cancelSubmitButton = document.querySelector("#cancel-exam-submit");
  const confirmSubmitButton = document.querySelector("#confirm-exam-submit");

  let current = Math.min(Math.max(config.currentPosition || 0, 0), questions.length - 1);
  let submitted = false;
  let submitting = false;
  let saveTimer = null;
  let socket = null;
  let reconnectDelay = 1000;
  let examStarted = Boolean(config.activated && config.expiresAt);
  let proctoringEnabled = false;
  let proctoringSuppressed = false;
  let incidentTimer = null;
  let pendingIncident = null;
  const eventQueue = [];

  const socketUrl = () => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${config.socketUrl}`;
  };

  const formatTime = (seconds) => {
    const safe = Math.max(0, seconds);
    const hours = Math.floor(safe / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const remainder = safe % 60;
    return hours > 0
      ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
      : `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  };

  const setNotice = (message, kind = "info") => {
    if (window.showMessage) window.showMessage(message, kind);
    else {
      notice.textContent = message;
      notice.dataset.kind = kind;
    }
  };

  const lockExam = (isExit = false) => {
    workspace.classList.add("is-locked");
    workspace.setAttribute("aria-hidden", "true");
    fullscreenGate.hidden = false;
    fullscreenGateTitle.textContent = examStarted
      ? "Return to fullscreen to continue"
      : "Enter fullscreen to start";
    fullscreenGateCopy.textContent = examStarted
      ? "Your exam timer is still running. Re-enter fullscreen to unlock the questions."
      : "Your exam timer starts only after fullscreen mode is active. Leaving fullscreen will lock and blur the exam.";
    fullscreenGateButton.textContent = examStarted
      ? "Re-enter fullscreen"
      : "Enter fullscreen and start exam";
    if (isExit) fullscreenGateButton.focus();
  };

  const unlockExam = () => {
    workspace.classList.remove("is-locked");
    workspace.setAttribute("aria-hidden", "false");
    fullscreenGate.hidden = true;
  };

  async function activateExam() {
    if (examStarted) return;
    const response = await fetch(config.activateUrl, {
      method: "POST",
      headers: { "X-CSRF-Token": config.csrfToken, Accept: "application/json" },
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "The exam could not be started.");
    config.expiresAt = result.expires_at;
    config.activated = true;
    examStarted = true;
    saveStatus.textContent = "All changes saved";
    connectSocket();
  }

  async function enterFullscreenAndContinue() {
    fullscreenGateButton.disabled = true;
    try {
      if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
      if (!document.fullscreenElement) throw new Error("Fullscreen mode is required to continue.");
      await activateExam();
      unlockExam();
      proctoringEnabled = true;
    } catch (error) {
      proctoringEnabled = false;
      lockExam();
      setNotice(error.message || "Fullscreen mode could not be enabled. Allow fullscreen access and try again.", "error");
      if (document.fullscreenElement && !examStarted) {
        try {
          await document.exitFullscreen();
        } catch (_) {
          // The gate remains locked if the browser cannot exit programmatically.
        }
      }
    } finally {
      fullscreenGateButton.disabled = false;
    }
  }

  const create = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };

  function renderQuestion() {
    const question = questions[current];
    card.replaceChildren();
    const heading = create("div", "question-heading");
    heading.append(create("span", "question-label", `Question ${current + 1}`));
    const clear = create("button", "button button-secondary clear-answer", "Clear answer");
    clear.style.marginLeft = "15px";
    clear.type = "button";
    clear.disabled = question.selected_option_id === null;
    clear.addEventListener("click", () => selectAnswer(null));
    heading.append(clear);
    card.append(heading);

    const title = create("h2", "question-text");
    question.text.split("\n").forEach((line, index) => {
      if (index) title.append(document.createElement("br"));
      title.append(document.createTextNode(line));
    });
    card.append(title);

    const optionList = create("div", "option-list");
    question.options.forEach((option) => {
      const label = create("label", "option-choice");
      if (question.selected_option_id === option.id) label.classList.add("selected");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = `question-${question.id}`;
      input.value = String(option.id);
      input.checked = question.selected_option_id === option.id;
      input.addEventListener("change", () => selectAnswer(option.id));
      label.append(input, create("span", "option-letter", option.display_label), create("span", "option-text", option.text));
      optionList.append(label);
    });
    card.append(optionList);

    counter.textContent = `Question ${current + 1} of ${questions.length}`;
    previousButton.disabled = current === 0;
    nextButton.textContent = current === questions.length - 1 ? "Review first question" : "Next question";
    renderPalette();
  }

  function renderPalette() {
    palette.replaceChildren();
    questions.forEach((question, index) => {
      const button = create("button", "palette-button", String(index + 1));
      button.type = "button";
      if (question.selected_option_id !== null) button.classList.add("answered");
      if (index === current) button.classList.add("current");
      button.setAttribute("aria-label", `Go to question ${index + 1}`);
      button.addEventListener("click", () => navigate(index));
      palette.append(button);
    });
    const answered = questions.filter((question) => question.selected_option_id !== null).length;
    answeredCount.textContent = String(answered);
    unansweredCount.textContent = String(questions.length - answered);
  }

  function selectAnswer(optionId) {
    questions[current].selected_option_id = optionId;
    renderQuestion();
    scheduleSave(current, 120);
  }

  function navigate(index) {
    saveQuestion(current);
    current = Math.min(Math.max(index, 0), questions.length - 1);
    renderQuestion();
    sendSocket({ type: "heartbeat", current_position: current });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function scheduleSave(index, delay = 600) {
    window.clearTimeout(saveTimer);
    saveStatus.textContent = "Saving…";
    saveTimer = window.setTimeout(() => saveQuestion(index), delay);
  }

  async function saveQuestion(index, keepalive = false) {
    if (submitted || !examStarted || !questions[index]) return;
    const question = questions[index];
    saveStatus.textContent = "Saving…";
    try {
      const response = await fetch(config.saveUrl, {
        method: "POST",
        keepalive,
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": config.csrfToken,
          Accept: "application/json",
        },
        body: JSON.stringify({
          question_id: question.id,
          selected_option_id: question.selected_option_id,
          time_spent_seconds: question.time_spent_seconds,
          current_position: current,
        }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "Save failed");
      saveStatus.textContent = "All changes saved";
    } catch (error) {
      saveStatus.textContent = "Waiting to reconnect";
      setNotice("Your latest change is stored in this browser and will retry when the connection returns.", "warning");
      try {
        localStorage.setItem(`exam-attempt-${config.attemptId}`, JSON.stringify(questions));
      } catch (_) {
        // Database autosave remains authoritative when storage is unavailable.
      }
    }
  }

  const openSubmitConfirmation = () => {
    if (submitted || submitting || !examStarted) return;
    proctoringSuppressed = true;
    submitConfirmation.hidden = false;
    confirmSubmitButton.focus();
  };

  const closeSubmitConfirmation = () => {
    submitConfirmation.hidden = true;
    submitButton.focus();
    window.setTimeout(() => { proctoringSuppressed = false; }, 400);
    if (!document.fullscreenElement) lockExam(true);
  };

  async function submitExam(automatic = false, confirmed = false) {
    if (submitted || submitting || !examStarted) return;
    if (!automatic && !confirmed) return openSubmitConfirmation();
    submitConfirmation.hidden = true;
    proctoringSuppressed = true;
    submitting = true;
    proctoringEnabled = false;
    submitButton.disabled = true;
    submitButton.textContent = automatic ? "Time is up — submitting…" : "Submitting…";
    await saveQuestion(current, true);
    submitted = true;
    try {
      const response = await fetch(config.submitUrl, {
        method: "POST",
        headers: { "X-CSRF-Token": config.csrfToken, Accept: "application/json" },
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "Submission failed");
      localStorage.removeItem(`exam-attempt-${config.attemptId}`);
      window.location.assign(result.redirect_url || config.resultUrl);
    } catch (error) {
      submitted = false;
      submitting = false;
      proctoringSuppressed = false;
      proctoringEnabled = Boolean(document.fullscreenElement);
      if (!proctoringEnabled) lockExam(true);
      submitButton.disabled = false;
      submitButton.textContent = "Submit exam";
      setNotice("Submission could not be confirmed. Check your connection and try again; your saved answers remain on the server.", "error");
    }
  }

  function sendSocket(payload) {
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
    else if (payload.type === "event") eventQueue.push(payload);
  }

  function proctorEvent(eventType, details = {}) {
    if (submitted || submitting || !examStarted || !proctoringEnabled || proctoringSuppressed) return;
    sendSocket({ type: "event", event_type: eventType, occurred_at: new Date().toISOString(), details });
  }

  const incidentPriority = { window_blur: 1, tab_hidden: 2, fullscreen_exit: 3 };
  function queueProctorIncident(eventType, details = {}) {
    if (submitted || submitting || !examStarted || !proctoringEnabled || proctoringSuppressed) return;
    if (!pendingIncident || incidentPriority[eventType] > incidentPriority[pendingIncident.eventType]) {
      pendingIncident = { eventType, details };
    }
    window.clearTimeout(incidentTimer);
    incidentTimer = window.setTimeout(() => {
      const incident = pendingIncident;
      pendingIncident = null;
      if (!incident || submitted || submitting || proctoringSuppressed) return;
      sendSocket({
        type: "event",
        event_type: incident.eventType,
        occurred_at: new Date().toISOString(),
        details: incident.details,
      });
    }, 350);
  }

  function connectSocket() {
    if (submitted || !examStarted || socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return;
    socket = new WebSocket(socketUrl());
    socket.addEventListener("open", () => {
      reconnectDelay = 1000;
      sendSocket({ type: "heartbeat", current_position: current });
      while (eventQueue.length) sendSocket(eventQueue.shift());
    });
    socket.addEventListener("close", () => {
      if (submitted) return;
      window.setTimeout(connectSocket, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    });
  }

  function restoreLocalDraft() {
    try {
      const draft = JSON.parse(localStorage.getItem(`exam-attempt-${config.attemptId}`));
      if (!Array.isArray(draft)) return;
      const byId = new Map(draft.map((item) => [item.id, item]));
      questions.forEach((question) => {
        const local = byId.get(question.id);
        if (local && local.time_spent_seconds >= question.time_spent_seconds) {
          question.selected_option_id = local.selected_option_id;
          question.time_spent_seconds = local.time_spent_seconds;
        }
      });
    } catch (_) {
      // Ignore invalid or unavailable browser recovery data.
    }
  }

  previousButton.addEventListener("click", () => navigate(current - 1));
  nextButton.addEventListener("click", () => navigate(current === questions.length - 1 ? 0 : current + 1));
  submitButton.addEventListener("click", openSubmitConfirmation);
  cancelSubmitButton.addEventListener("click", closeSubmitConfirmation);
  confirmSubmitButton.addEventListener("click", () => submitExam(false, true));
  submitConfirmation.addEventListener("click", (event) => {
    if (event.target === submitConfirmation) closeSubmitConfirmation();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !submitConfirmation.hidden) closeSubmitConfirmation();
  });
  fullscreenGateButton.addEventListener("click", enterFullscreenAndContinue);

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) queueProctorIncident("tab_hidden", { state: document.visibilityState });
  });
  window.addEventListener("blur", () => queueProctorIncident("window_blur"));
  document.addEventListener("fullscreenchange", () => {
    if (examStarted && !document.fullscreenElement) {
      lockExam(true);
      queueProctorIncident("fullscreen_exit");
      proctoringEnabled = false;
    }
  });
  document.addEventListener("copy", () => proctorEvent("copy_attempt"));
  document.addEventListener("paste", () => proctorEvent("paste_attempt"));
  document.addEventListener("contextmenu", () => proctorEvent("context_menu"));
  window.addEventListener("pagehide", () => saveQuestion(current, true));
  window.addEventListener("beforeunload", (event) => {
    if (examStarted && !submitted) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  const navigationEntry = performance.getEntriesByType("navigation")[0];
  if (navigationEntry?.type === "reload" && examStarted) {
    eventQueue.push({ type: "event", event_type: "page_reload", occurred_at: new Date().toISOString(), details: {} });
  }

  window.setInterval(() => {
    if (examStarted && !submitted && questions[current]) questions[current].time_spent_seconds += 1;
  }, 1000);
  window.setInterval(() => {
    if (examStarted && !submitted) {
      saveQuestion(current);
      sendSocket({ type: "heartbeat", current_position: current });
    }
  }, 10000);
  window.setInterval(() => {
    if (!examStarted || !config.expiresAt) {
      timer.textContent = "Not started";
      return;
    }
    const remaining = Math.max(0, Math.ceil((new Date(config.expiresAt).getTime() - Date.now()) / 1000));
    timer.textContent = formatTime(remaining);
    timer.parentElement.classList.toggle("timer-warning", remaining <= 300);
    if (remaining <= 0) submitExam(true);
  }, 250);

  restoreLocalDraft();
  renderQuestion();
  lockExam();
})();
