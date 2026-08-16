(() => {
  "use strict";

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const messageModal = document.querySelector("#app-message-modal");
  const messageTitle = document.querySelector("#app-message-title");
  const messageText = document.querySelector("#app-message-text");
  const messageIcon = document.querySelector("#app-message-icon");
  const messageProgress = document.querySelector("#app-message-progress");
  const messageKinds = {
    success: { title: "Success", icon: "✓" },
    error: { title: "Action needed", icon: "!" },
    warning: { title: "Please note", icon: "!" },
    info: { title: "Notice", icon: "i" },
  };
  let messageTimer = null;
  let messageHideTimer = null;

  const hideMessage = () => {
    if (!messageModal || messageModal.hidden) return;
    window.clearTimeout(messageTimer);
    window.clearTimeout(messageHideTimer);
    messageModal.classList.remove("is-visible");
    messageHideTimer = window.setTimeout(() => { messageModal.hidden = true; }, 180);
  };

  window.showMessage = (message, kind = "info", timeout = 3000) => {
    if (!messageModal || !message) return;
    const normalizedKind = messageKinds[kind] ? kind : "info";
    const presentation = messageKinds[normalizedKind];
    window.clearTimeout(messageTimer);
    window.clearTimeout(messageHideTimer);
    messageModal.hidden = false;
    messageModal.dataset.kind = normalizedKind;
    messageTitle.textContent = presentation.title;
    messageText.textContent = String(message);
    messageIcon.textContent = presentation.icon;
    const progressBar = document.createElement("span");
    progressBar.style.animationDuration = `${timeout}ms`;
    messageProgress.replaceChildren(progressBar);
    window.requestAnimationFrame(() => messageModal.classList.add("is-visible"));
    messageTimer = window.setTimeout(hideMessage, timeout);
  };

  messageModal?.querySelectorAll("[data-message-close]").forEach((button) => {
    button.addEventListener("click", hideMessage);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !messageModal?.hidden) hideMessage();
  });
  if (messageModal && !messageModal.hidden && messageText.textContent.trim()) {
    window.showMessage(messageText.textContent.trim(), messageModal.dataset.kind);
  }

  let invalidMessageShown = false;
  document.addEventListener("invalid", (event) => {
    event.preventDefault();
    if (invalidMessageShown) return;
    invalidMessageShown = true;
    const field = event.target;
    window.showMessage(field.validationMessage || "Complete the required field.", "error");
    field.focus({ preventScroll: false });
    window.setTimeout(() => { invalidMessageShown = false; }, 0);
  }, true);

  document.querySelectorAll(".js-start-exam").forEach((button) => {
    button.addEventListener("click", async () => {
      const examId = button.dataset.examId;
      button.disabled = true;
      button.textContent = "Preparing your exam…";
      try {
        const response = await fetch(`/student/exams/${examId}/start`, {
          method: "POST",
          headers: { "X-CSRF-Token": csrfToken, Accept: "application/json" },
        });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.error || "Unable to start exam.");
        window.location.assign(result.redirect_url);
      } catch (error) {
        button.disabled = false;
        button.textContent = "Try again";
        window.showMessage(error.message || "Unable to start exam.", "error");
      }
    });
  });

  document.querySelectorAll(".js-confirm-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm || "Continue with this action?")) {
        event.preventDefault();
      }
    });
  });

  const questionPaperToggle = document.querySelector("#question-paper-toggle");
  const questionPaperList = document.querySelector("#question-paper-list");
  questionPaperToggle?.addEventListener("click", () => {
    const isOpening = questionPaperList.hidden;
    questionPaperList.hidden = !isOpening;
    questionPaperToggle.setAttribute("aria-expanded", String(isOpening));
    questionPaperToggle.textContent = isOpening ? "Hide question paper" : "Show question paper";
  });

  const examTimeline = document.querySelector(".manage-page-header[data-exam-start-at]");
  const examTime = document.querySelector("#manage-exam-time");
  const examStatus = document.querySelector("#manage-exam-status");
  const compactTime = (milliseconds) => {
    const totalMinutes = Math.max(1, Math.ceil(milliseconds / 60000));
    const days = Math.floor(totalMinutes / 1440);
    const remainingMinutes = totalMinutes % 1440;
    const hours = Math.floor(remainingMinutes / 60);
    const minutes = remainingMinutes % 60;
    const parts = [];
    if (days) parts.push(`${days} ${days === 1 ? "day" : "days"}`);
    if (hours) parts.push(`${hours} hr`);
    if (minutes || !parts.length) parts.push(`${minutes} min`);
    return parts.join(" ");
  };
  const refreshExamTimeline = () => {
    if (!examTimeline || !examTime || !examStatus) return;
    const startAt = new Date(examTimeline.dataset.examStartAt);
    const endAt = new Date(examTimeline.dataset.examEndAt);
    if (Number.isNaN(startAt.getTime()) || Number.isNaN(endAt.getTime())) return;
    const now = new Date();
    let label = "Published";
    let tone = "warning";
    if (examTimeline.dataset.examEnded === "true" || now >= endAt) {
      label = "Ended";
      tone = "danger";
      examTime.hidden = true;
    } else if (now >= startAt) {
      label = "Running";
      tone = "success";
      examTime.hidden = false;
      examTime.textContent = `Ends in · ${compactTime(endAt - now)}`;
    } else {
      examTime.hidden = false;
      examTime.textContent = `Starts in · ${compactTime(startAt - now)}`;
    }
    examStatus.textContent = `Status · ${label}`;
    examStatus.classList.remove("success", "warning", "danger", "neutral");
    examStatus.classList.add(tone);
    examTime.classList.remove("timer-published", "timer-running", "timer-ended");
    examTime.classList.add(`timer-${label.toLowerCase()}`);
  };
  refreshExamTimeline();
  if (examTimeline) window.setInterval(refreshExamTimeline, 30000);

  const refreshStudentCountdowns = () => {
    document.querySelectorAll(".student-start-countdown[data-start-at]").forEach((countdown) => {
      const startAt = new Date(countdown.dataset.startAt);
      if (Number.isNaN(startAt.getTime())) return;
      const remaining = startAt - new Date();
      if (remaining <= 0) {
        countdown.hidden = true;
        return;
      }
      countdown.textContent = `Starts in · ${compactTime(remaining)}`;
    });
  };
  refreshStudentCountdowns();
  if (document.querySelector(".student-start-countdown")) {
    window.setInterval(refreshStudentCountdowns, 30000);
  }

  const refreshExaminerCountdowns = () => {
    document.querySelectorAll(".examiner-exam-card, .examiner-monitor-countdown").forEach((timeline) => {
      const countdown = timeline.classList.contains("examiner-exam-countdown")
        ? timeline
        : timeline.querySelector(".examiner-exam-countdown");
      const status = timeline.classList.contains("examiner-exam-card")
        ? timeline.querySelector(".examiner-card-status")
        : document.querySelector(".monitor-exam-capsules .examiner-card-status");
      if (!countdown) return;
      const startAt = new Date(timeline.dataset.examStartAt);
      const endAt = new Date(timeline.dataset.examEndAt);
      if (Number.isNaN(startAt.getTime()) || Number.isNaN(endAt.getTime())) return;
      const now = new Date();
      let label = "Published";
      let tone = "warning";
      if (timeline.dataset.examEnded === "true" || now >= endAt) {
        label = "Ended";
        tone = "danger";
        countdown.hidden = true;
      } else if (now >= startAt) {
        label = "Running";
        tone = "success";
        countdown.hidden = false;
        countdown.textContent = `Ends in · ${compactTime(endAt - now)}`;
      } else {
        countdown.hidden = false;
        countdown.textContent = `Starts in · ${compactTime(startAt - now)}`;
      }
      countdown.classList.remove("timer-published", "timer-running", "timer-ended");
      countdown.classList.add(`timer-${label.toLowerCase()}`);
      if (status) {
        status.textContent = `Status · ${label}`;
        status.classList.remove("success", "warning", "danger", "neutral");
        status.classList.add(tone);
      }
    });
  };
  refreshExaminerCountdowns();
  if (document.querySelector(".examiner-exam-countdown")) {
    window.setInterval(refreshExaminerCountdowns, 30000);
  }

  const scheduleField = document.querySelector("#schedule-field");
  const startValue = document.querySelector("#start-at-value");
  const uploadForm = scheduleField?.closest("form");
  const pageLoadedAt = Date.now();

  const localDateFromIso = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
    return match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5])) : null;
  };

  const serverNow = localDateFromIso(scheduleField?.dataset.serverNow) || new Date();
  const liveServerNow = () => new Date(serverNow.getTime() + (Date.now() - pageLoadedAt));

  const toLocalIso = (date) => {
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };

  const validateSchedule = (announce = false) => {
    if (!startValue) return true;
    const selected = localDateFromIso(startValue.value);
    if (!selected || Number.isNaN(selected.getTime()) || selected <= liveServerNow()) {
      scheduleField.classList.add("has-error");
      if (announce) window.showMessage("Choose a date and time in the future.", "error");
      return false;
    }
    scheduleField.classList.remove("has-error");
    return true;
  };

  if (startValue) startValue.min = toLocalIso(liveServerNow());
  startValue?.addEventListener("change", () => validateSchedule());
  document.querySelectorAll("[data-schedule-offset]").forEach((button) => {
    button.addEventListener("click", () => {
      const selected = new Date(liveServerNow());
      selected.setMinutes(selected.getMinutes() + Number(button.dataset.scheduleOffset));
      selected.setSeconds(0, 0);
      startValue.value = toLocalIso(selected);
      validateSchedule();
    });
  });
  uploadForm?.addEventListener("submit", (event) => {
    if (!validateSchedule(true)) {
      event.preventDefault();
      scheduleField.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });
})();
