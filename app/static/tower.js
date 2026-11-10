// Small client glue: tenant switcher, live indicator, feed cap, replay player.
(function () {
  const tenant = document.getElementById("tenant");
  if (tenant) tenant.addEventListener("change", () => {
    document.cookie = "t=" + tenant.value + ";path=/;max-age=2592000;samesite=lax";
    const url = new URL(location.href);
    url.searchParams.set("t", tenant.value);
    location.href = url.toString();
  });

  const live = document.getElementById("live");
  document.body.addEventListener("htmx:sseOpen", () => live && live.classList.remove("off"));
  document.body.addEventListener("htmx:sseError", () => live && live.classList.add("off"));

  // Keep the live feed bounded and flash new rows.
  document.body.addEventListener("htmx:sseMessage", (e) => {
    if (e.detail.type !== "feed") return;
    document.querySelectorAll(".feed").forEach((ul) => {
      const first = ul.firstElementChild;
      if (first) first.classList.add("new");
      while (ul.children.length > 120) ul.removeChild(ul.lastElementChild);
      const empty = ul.parentElement.querySelector(".empty");
      if (empty) empty.remove();
    });
  });

  // Replay player: click a step to inspect it; play steps through at operator speed.
  let timer = null;
  function select(idx) {
    const steps = document.querySelectorAll(".steps li");
    if (!steps.length) return;
    idx = Math.max(0, Math.min(idx, steps.length - 1));
    steps.forEach((s, i) => s.classList.toggle("on", i === idx));
    const tpl = document.getElementById("step-" + steps[idx].dataset.id);
    const out = document.getElementById("step-detail");
    if (tpl && out) out.innerHTML = tpl.innerHTML;
    steps[idx].scrollIntoView({ block: "nearest" });
    const pos = document.getElementById("step-pos");
    if (pos) pos.textContent = (idx + 1) + " / " + steps.length;
    window.__step = idx;
  }
  function current() { return window.__step || 0; }
  function stop() { clearInterval(timer); timer = null; const b = document.getElementById("play"); if (b) b.textContent = "▶ Play"; }
  document.addEventListener("click", (e) => {
    const li = e.target.closest(".steps li");
    if (li) { stop(); select([...li.parentElement.children].indexOf(li)); return; }
    const id = e.target.id;
    if (id === "prev") { stop(); select(current() - 1); }
    if (id === "next") { stop(); select(current() + 1); }
    if (id === "first") { stop(); select(0); }
    if (id === "play") {
      if (timer) return stop();
      e.target.textContent = "❚❚ Pause";
      const n = document.querySelectorAll(".steps li").length;
      if (current() >= n - 1) select(0);
      timer = setInterval(() => {
        if (current() >= document.querySelectorAll(".steps li").length - 1) return stop();
        select(current() + 1);
      }, 1100);
    }
  });
  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.target.id === "replay") { stop(); select(0); }
  });
})();
