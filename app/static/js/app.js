(() => {
  "use strict";

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  document.querySelectorAll(".js-start-exam").forEach((button) => {
    button.addEventListener("click", async () => {
      const examId = button.dataset.examId;
      const message = document.querySelector(`[data-message-for="${examId}"]`);
      button.disabled = true;
      button.textContent = "Preparing your exam…";
      if (message) message.textContent = "";
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
        if (message) message.textContent = error.message;
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

  const examType = document.querySelector("#exam-type");
  const scheduleField = document.querySelector("#schedule-field");
  const graceField = document.querySelector("#grace-field");
  const startValue = document.querySelector("#start-at-value");
  const scheduleError = document.querySelector("#schedule-error");
  const scheduleModeCopy = document.querySelector("#schedule-mode-copy");
  const uploadForm = scheduleField?.closest("form");
  const monthSelect = document.querySelector("#start-month");
  const daySelect = document.querySelector("#start-day");
  const yearSelect = document.querySelector("#start-year");
  const hourSelect = document.querySelector("#start-hour");
  const minuteSelect = document.querySelector("#start-minute");
  const periodSelect = document.querySelector("#start-period");
  const scheduleControls = [...document.querySelectorAll("[data-schedule-control]")];
  const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const pageLoadedAt = Date.now();

  const localDateFromIso = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
    return match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5])) : null;
  };

  const serverNow = localDateFromIso(scheduleField?.dataset.serverNow) || new Date();
  const liveServerNow = () => new Date(serverNow.getTime() + (Date.now() - pageLoadedAt));

  const replaceOptions = (select, values, labelFor = String) => {
    if (!select) return;
    const selected = select.value;
    select.replaceChildren(...values.map((value) => {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = labelFor(value);
      return option;
    }));
    if ([...select.options].some((option) => option.value === selected)) select.value = selected;
  };

  const refreshDays = () => {
    if (!daySelect || !monthSelect || !yearSelect) return;
    const previousDay = Number(daySelect.value || 1);
    const dayCount = new Date(Number(yearSelect.value), Number(monthSelect.value), 0).getDate();
    replaceOptions(daySelect, Array.from({ length: dayCount }, (_, index) => index + 1));
    daySelect.value = String(Math.min(previousDay, dayCount));
  };

  const setPickerDate = (date) => {
    if (!monthSelect) return;
    yearSelect.value = String(date.getFullYear());
    monthSelect.value = String(date.getMonth() + 1);
    refreshDays();
    daySelect.value = String(date.getDate());
    const hour24 = date.getHours();
    hourSelect.value = String((hour24 % 12) || 12);
    minuteSelect.value = String(Math.ceil(date.getMinutes() / 5) * 5 % 60).padStart(2, "0");
    periodSelect.value = hour24 >= 12 ? "PM" : "AM";
  };

  const pickerDate = () => {
    if (!monthSelect) return null;
    let hour = Number(hourSelect.value);
    if (periodSelect.value === "AM" && hour === 12) hour = 0;
    if (periodSelect.value === "PM" && hour !== 12) hour += 12;
    return new Date(Number(yearSelect.value), Number(monthSelect.value) - 1, Number(daySelect.value), hour, Number(minuteSelect.value));
  };

  const toLocalIso = (date) => {
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };

  const validateSchedule = () => {
    if (!startValue || examType?.value !== "scheduled") return true;
    const selected = pickerDate();
    if (!selected || Number.isNaN(selected.getTime()) || selected <= liveServerNow()) {
      startValue.value = "";
      scheduleError.textContent = "Choose a date and time in the future.";
      scheduleError.hidden = false;
      scheduleField.classList.add("has-error");
      return false;
    }
    startValue.value = toLocalIso(selected);
    scheduleError.hidden = true;
    scheduleField.classList.remove("has-error");
    return true;
  };

  const initialiseSchedulePicker = () => {
    if (!monthSelect) return;
    replaceOptions(monthSelect, Array.from({ length: 12 }, (_, index) => index + 1), (value) => monthNames[value - 1]);
    replaceOptions(yearSelect, Array.from({ length: 6 }, (_, index) => serverNow.getFullYear() + index));
    replaceOptions(hourSelect, Array.from({ length: 12 }, (_, index) => index + 1));
    replaceOptions(minuteSelect, Array.from({ length: 12 }, (_, index) => String(index * 5).padStart(2, "0")));
    const initial = localDateFromIso(startValue?.value) || new Date(serverNow.getTime() + 30 * 60 * 1000);
    initial.setMinutes(Math.ceil(initial.getMinutes() / 5) * 5, 0, 0);
    setPickerDate(initial);
    scheduleControls.forEach((control) => control.addEventListener("change", () => {
      if (control === monthSelect || control === yearSelect) refreshDays();
      validateSchedule();
    }));
    document.querySelectorAll("[data-schedule-offset]").forEach((button) => {
      button.addEventListener("click", () => {
        const choice = button.dataset.scheduleOffset;
        const selected = new Date(liveServerNow());
        if (choice === "tomorrow") {
          selected.setDate(selected.getDate() + 1);
          selected.setHours(9, 0, 0, 0);
        } else {
          selected.setMinutes(selected.getMinutes() + Number(choice));
          selected.setMinutes(Math.ceil(selected.getMinutes() / 5) * 5, 0, 0);
        }
        setPickerDate(selected);
        validateSchedule();
      });
    });
  };

  const syncScheduleFields = () => {
    if (!examType || !scheduleField || !graceField || !startValue) return;
    const scheduled = examType.value === "scheduled";
    scheduleField.classList.toggle("is-disabled", !scheduled);
    scheduleField.setAttribute("aria-disabled", String(!scheduled));
    scheduleControls.forEach((control) => { control.disabled = !scheduled; });
    graceField.querySelector("input").disabled = !scheduled;
    startValue.disabled = !scheduled;
    scheduleModeCopy.textContent = scheduled
      ? `Times are shown in ${scheduleField.dataset.timezone}. Past dates cannot be selected.`
      : "Scheduling is unavailable for practice exams.";
    if (scheduled) validateSchedule();
    else {
      scheduleError.hidden = true;
      scheduleField.classList.remove("has-error");
    }
  };
  initialiseSchedulePicker();
  examType?.addEventListener("change", syncScheduleFields);
  uploadForm?.addEventListener("submit", (event) => {
    if (examType?.value === "scheduled" && !validateSchedule()) {
      event.preventDefault();
      scheduleField.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });
  syncScheduleFields();
})();
