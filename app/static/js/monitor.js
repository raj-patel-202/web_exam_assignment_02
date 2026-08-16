(() => {
  "use strict";

  const config = JSON.parse(document.querySelector("#monitor-config").textContent);
  let state = JSON.parse(document.querySelector("#monitor-snapshot").textContent);
  const body = document.querySelector("#monitor-student-body");
  const eventsContainer = document.querySelector("#monitor-events");
  const empty = document.querySelector("#monitor-empty");
  const countLabel = document.querySelector("#student-count-label");
  const filters = document.querySelector("#monitor-filters");
  let activeFilter = "all";
  let socket = null;
  let reconnectDelay = 1000;

  const socketUrl = () => `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}${config.socketUrl}`;
  const relativeTime = (value) => {
    if (!value) return "No heartbeat";
    const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
    if (seconds < 5) return "Just now";
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };
  const eventLabel = (type) => ({
    tab_hidden: "Tab hidden",
    tab_visible: "Tab visible again",
    window_blur: "Window lost focus",
    window_focus: "Window focused again",
    fullscreen_exit: "Exited fullscreen",
    copy_attempt: "Copy attempt",
    paste_attempt: "Paste attempt",
    context_menu: "Context menu opened",
    page_reload: "Exam page reloaded",
    disconnected: "Connection lost",
    reconnected: "Connection restored",
  }[type] || type.replaceAll("_", " "));

  function setText(selector, value) {
    const element = document.querySelector(selector);
    if (element) element.textContent = String(value);
  }

  function renderMetrics() {
    const total = state.attempts.length;
    const online = state.attempts.filter((attempt) => attempt.online).length;
    const submitted = state.attempts.filter((attempt) => attempt.status !== "in_progress").length;
    const flags = state.attempts.reduce((sum, attempt) => sum + attempt.flags, 0);
    setText('[data-metric="total"]', total);
    setText('[data-metric="online"]', online);
    setText('[data-metric="submitted"]', submitted);
    setText('[data-metric="flags"]', flags);
  }

  function included(attempt) {
    if (activeFilter === "flagged") return attempt.flags > 0;
    if (activeFilter === "offline") return attempt.activated && !attempt.online && attempt.status === "in_progress";
    if (activeFilter === "submitted") return attempt.status !== "in_progress";
    return true;
  }

  function renderStudents() {
    body.replaceChildren();
    const rows = state.attempts.filter(included).sort((a, b) => b.flags - a.flags || Number(b.online) - Number(a.online));
    rows.forEach((attempt) => {
      const row = document.createElement("tr");
      if (attempt.flags) row.classList.add("flagged-row");
      const identity = document.createElement("td");
      const name = document.createElement("strong");
      name.textContent = attempt.student_name;
      const username = document.createElement("small");
      username.textContent = attempt.username;
      identity.append(name, username);

      const status = document.createElement("td");
      const statusPill = document.createElement("span");
      const isSubmitted = attempt.status !== "in_progress";
      const isWaiting = !isSubmitted && !attempt.activated;
      statusPill.className = `status-pill ${isSubmitted ? "neutral" : isWaiting ? "warning" : attempt.online ? "success" : "danger"}`;
      statusPill.textContent = isSubmitted ? "Submitted" : isWaiting ? "Waiting to start" : attempt.online ? "Online" : "Disconnected";
      status.append(statusPill);

      const progress = document.createElement("td");
      const progressTrack = document.createElement("div");
      progressTrack.className = "progress-track";
      const progressFill = document.createElement("span");
      progressFill.style.width = `${attempt.total_questions ? (100 * attempt.answered) / attempt.total_questions : 0}%`;
      progressTrack.append(progressFill);
      const progressText = document.createElement("small");
      progressText.textContent = `${attempt.answered}/${attempt.total_questions} answered`;
      progress.append(progressTrack, progressText);

      const question = document.createElement("td");
      question.textContent = `Q${attempt.current_question} of ${attempt.total_questions}`;
      const heartbeat = document.createElement("td");
      heartbeat.textContent = relativeTime(attempt.last_heartbeat_at);
      const flags = document.createElement("td");
      const flagPill = document.createElement("span");
      flagPill.className = `flag-count ${attempt.flags ? "has-flags" : ""}`;
      flagPill.textContent = String(attempt.flags);
      flags.append(flagPill);
      row.append(identity, status, progress, question, heartbeat, flags);
      body.append(row);
    });
    empty.hidden = rows.length > 0;
    countLabel.textContent = `${rows.length} session${rows.length === 1 ? "" : "s"}`;
  }

  function renderEvents() {
    eventsContainer.replaceChildren();
    const events = state.events.slice(0, 50);
    if (!events.length) {
      const placeholder = document.createElement("div");
      placeholder.className = "event-empty";
      placeholder.textContent = "No browser-focus incidents recorded yet.";
      eventsContainer.append(placeholder);
      return;
    }
    events.forEach((event) => {
      const item = document.createElement("article");
      item.className = `event-item severity-${event.severity}`;
      const dot = document.createElement("span");
      const copy = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = eventLabel(event.event_type);
      const detail = document.createElement("p");
      detail.textContent = event.student_name;
      const time = document.createElement("time");
      time.textContent = relativeTime(event.occurred_at);
      copy.append(title, detail);
      item.append(dot, copy, time);
      eventsContainer.append(item);
    });
  }

  function render() {
    renderMetrics();
    renderStudents();
    renderEvents();
  }

  function upsertAttempt(attempt) {
    const index = state.attempts.findIndex((item) => item.attempt_id === attempt.attempt_id);
    if (index >= 0) state.attempts[index] = attempt;
    else state.attempts.unshift(attempt);
  }

  function connect() {
    socket = new WebSocket(socketUrl());
    socket.addEventListener("open", () => {
      reconnectDelay = 1000;
    });
    socket.addEventListener("message", (message) => {
      const payload = JSON.parse(message.data);
      if (payload.type === "snapshot") {
        state = { attempts: payload.attempts || [], events: payload.events || [] };
      } else if (payload.type === "attempt_update") {
        upsertAttempt(payload.attempt);
      } else if (payload.type === "proctor_event") {
        if (!state.events.some((event) => event.id === payload.event.id)) state.events.unshift(payload.event);
      }
      render();
    });
    socket.addEventListener("close", (event) => {
      if (event.code === 4403) {
        state = { attempts: [], events: [] };
        render();
        return;
      }
      window.setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    });
  }

  filters.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-filter]");
    if (!button) return;
    activeFilter = button.dataset.filter;
    filters.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
    renderStudents();
  });
  document.querySelector("#refresh-monitor").addEventListener("click", async () => {
    try {
      const response = await fetch(config.snapshotUrl, { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      state = await response.json();
      render();
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "refresh" }));
    } catch (_) {
      // WebSocket reconnection continues independently.
    }
  });
  window.setInterval(() => renderStudents(), 5000);
  render();
  connect();
})();
