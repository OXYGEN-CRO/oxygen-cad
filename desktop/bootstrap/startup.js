const status = document.getElementById('status');
const retry = document.getElementById('retry');
retry.onclick = () => window.__TAURI__.core.invoke('retry');
async function update() {
  try {
    const state = JSON.parse(await window.__TAURI__.core.invoke('startup_status'));
    status.textContent = state.message;
    retry.hidden = !state.error;
  } catch (error) { status.textContent = String(error); }
  setTimeout(update, 350);
}
update();
