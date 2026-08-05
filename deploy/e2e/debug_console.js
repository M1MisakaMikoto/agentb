const token = document.body.dataset.token;
const headers = { "X-Debug-Token": token };
const connection = document.querySelector("#connection");
const services = document.querySelector("#services");
const log = document.querySelector("#log");
const jobMeta = document.querySelector("#job-meta");
const actionButtons = [...document.querySelectorAll("[data-action]")];
const cancelButton = document.querySelector("#cancel");

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function stateClass(value) {
  return `state-${String(value || "").toLowerCase().replace(/[^a-z]+/g, "-")}`;
}

async function refreshStatus() {
  try {
    connection.textContent = "控制台在线";
    const payload = await api("/api/status");
    services.replaceChildren();
    if (!payload.services.length) {
      services.innerHTML = '<tr><td colspan="4">没有运行中的 Compose 服务</td></tr>';
      return;
    }
    for (const service of payload.services) {
      const row = document.createElement("tr");
      const values = [service.Service, service.State, service.Health || "-", service.Name];
      values.forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value || "-";
        if (index === 1 || index === 2) cell.className = stateClass(value);
        row.appendChild(cell);
      });
      services.appendChild(row);
    }
  } catch (error) {
    connection.textContent = `状态错误: ${error.message}`;
    connection.className = "connection error";
  }
}

async function refreshJob() {
  try {
    const { job } = await api("/api/job");
    const running = Boolean(job && job.running);
    actionButtons.forEach((button) => { button.disabled = running; });
    cancelButton.disabled = !running;
    if (!job) return;
    const status = running ? "运行中" : `退出码 ${job.exit_code}`;
    jobMeta.innerHTML = "";
    for (const value of [`任务 ${job.job_id}`, job.action, status]) {
      const span = document.createElement("span");
      span.textContent = value;
      jobMeta.appendChild(span);
    }
    const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    log.textContent = job.lines.join("\n") || "任务已启动，等待输出";
    if (nearBottom) log.scrollTop = log.scrollHeight;
    if (!running) refreshStatus();
  } catch (error) {
    log.textContent = `任务状态错误: ${error.message}`;
  }
}

for (const button of actionButtons) {
  button.addEventListener("click", async () => {
    try {
      await api(`/api/actions/${button.dataset.action}`, { method: "POST" });
      await refreshJob();
    } catch (error) {
      log.textContent = `无法启动任务: ${error.message}`;
    }
  });
}

cancelButton.addEventListener("click", async () => {
  try {
    await api("/api/job/cancel", { method: "POST" });
  } catch (error) {
    log.textContent = `无法取消任务: ${error.message}`;
  }
  await refreshJob();
});

document.querySelector("#refresh").addEventListener("click", refreshStatus);
refreshStatus();
refreshJob();
setInterval(refreshJob, 1500);
setInterval(refreshStatus, 10000);
