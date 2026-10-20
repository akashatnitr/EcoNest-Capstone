(() => {
  const actions = [
    ["resident_arrives", "Resident arrives"],
    ["resident_leaves", "Resident leaves"],
    ["heat_wave", "Start heat wave"],
    ["advance_ten_minutes", "Advance 10 min"],
    ["reset", "Reset scenario"],
  ];

  function addControls() {
    if (document.querySelector("[data-simulation-controls]") || !document.querySelector(".plan")) return;
    const side = document.querySelector(".side");
    if (!side) return;
    const controls = document.createElement("section");
    controls.className = "card";
    controls.dataset.simulationControls = "true";
    controls.style.padding = "18px";
    controls.innerHTML = `<h3>Try a scenario</h3><p style="margin:0 0 12px;color:#69655b;font-size:.86rem;line-height:1.45">These controls change only this simulated apartment.</p><div style="display:grid;gap:8px">${actions.map(([action, label]) => `<button type="button" data-simulation-action="${action}" style="padding:9px;text-align:left;font:inherit;font-weight:700">${label}</button>`).join("")}</div><p data-simulation-status style="min-height:1.2em;margin:12px 0 0;color:#69655b;font-size:.78rem"></p>`;
    controls.querySelector("[data-simulation-status]").textContent = window.simulationScenarioFeedback || "";
    side.prepend(controls);
    controls.querySelectorAll("[data-simulation-action]").forEach(button => {
      button.addEventListener("click", async () => {
        const status = controls.querySelector("[data-simulation-status]");
        const buttons = controls.querySelectorAll("button");
        buttons.forEach(item => { item.disabled = true; });
        status.textContent = "Updating the simulation…";
        try {
          const activeResponse = await fetch("/auth/active-household", {
            cache: "no-store",
          });
          const activeHousehold = activeResponse.ok
            ? await activeResponse.json()
            : null;
          const response = await fetch("/simulation/events", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              action: button.dataset.simulationAction,
              household_id: Number(activeHousehold?.id),
            }),
          });
          if (!response.ok) throw new Error("Simulation update failed");
          const updated = await response.json();
          const event = updated.events?.[0] || `${button.textContent.trim()} applied.`;
          window.simulationScenarioFeedback = `Applied: ${event.replace(/^Now · /, "")}`;
          window.render(updated);
        } catch {
          status.textContent = "The simulated event could not be applied. Try again.";
          buttons.forEach(item => { item.disabled = false; });
        }
      });
    });
  }

  const observer = new MutationObserver(addControls);
  observer.observe(document.body, { childList: true, subtree: true });
  addControls();
})();
