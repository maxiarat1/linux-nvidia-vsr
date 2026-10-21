async function update() {
  try {
    const r = await fetch("/health", { cache: "no-store" });
    const s = await r.json();
    document.querySelector("#state").textContent = JSON.stringify(s, null, 2);
  } catch (_) {
    document.querySelector("#state").textContent = "Local engine is offline.";
  }
}
update();
setInterval(update, 2000);
