(() => {
  "use strict";

  // Confirmación explícita para acciones administrativas irreversibles.
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const message = form.getAttribute("data-confirm");
      if (message && !window.confirm(message)) {
        event.preventDefault();
      }
    });
  });

  // Límite de selecciones en la papeleta y contador visible.
  document.querySelectorAll("[data-ballot-form]").forEach((form) => {
    const groups = new Map();
    form.querySelectorAll("[data-max-group]").forEach((input) => {
      const group = input.getAttribute("data-max-group");
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(input);
    });

    const counters = new Map();
    form.querySelectorAll("[data-counter-for]").forEach((counter) => {
      counters.set(counter.getAttribute("data-counter-for"), counter);
    });

    const maxFor = (group) => {
      const counter = counters.get(group);
      return counter ? Number.parseInt(counter.getAttribute("data-max"), 10) : 1;
    };

    const update = (group) => {
      const inputs = groups.get(group) || [];
      const checked = inputs.filter((input) => input.checked);
      const counter = counters.get(group);
      if (counter) {
        const max = maxFor(group);
        counter.textContent = `${checked.length} / ${max}`;
        counter.classList.toggle("complete", checked.length === max);
      }
    };

    groups.forEach((inputs, group) => {
      inputs.forEach((input) => {
        input.addEventListener("change", () => {
          const max = maxFor(group);
          const checked = inputs.filter((item) => item.checked);
          if (checked.length > max) {
            input.checked = false;
            input.setCustomValidity(`Puede seleccionar como máximo ${max} candidato(s).`);
            input.reportValidity();
            window.setTimeout(() => input.setCustomValidity(""), 1500);
          } else {
            input.setCustomValidity("");
          }
          update(group);
        });
      });
      update(group);
    });

    form.addEventListener("submit", (event) => {
      let valid = true;
      groups.forEach((inputs, group) => {
        const checked = inputs.filter((input) => input.checked).length;
        if (checked !== maxFor(group)) {
          valid = false;
          const counter = counters.get(group);
          if (counter) counter.classList.add("complete");
          window.alert(`Debe seleccionar exactamente ${maxFor(group)} candidato(s).`);
        }
      });
      if (!valid) event.preventDefault();
    });
  });

  // Copiar un comprobante individual.
  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = button.getAttribute("data-copy");
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        const original = button.textContent;
        button.textContent = "Copiado";
        window.setTimeout(() => { button.textContent = original; }, 1400);
      } catch (_error) {
        window.prompt("Copie el código manualmente:", value);
      }
    });
  });

  // Copiar una lista completa de tokens.
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.querySelector(button.getAttribute("data-copy-target"));
      if (!target) return;
      try {
        await navigator.clipboard.writeText(target.value);
        const original = button.textContent;
        button.textContent = "Copiado";
        window.setTimeout(() => { button.textContent = original; }, 1400);
      } catch (_error) {
        target.focus();
        target.select();
        window.prompt("Copie los tokens manualmente:", target.value);
      }
    });
  });

  document.querySelectorAll("[data-print]").forEach((button) => {
    button.addEventListener("click", () => window.print());
  });

  // Tamaño de texto para personas mayores. Se guarda en el dispositivo para
  // que la preferencia siga aplicada en la siguiente visita.
  const sizeButtons = document.querySelectorAll("[data-text-size]");
  const applyTextSize = (size) => {
    if (size === "normal") {
      document.documentElement.removeAttribute("data-text-size");
    } else {
      document.documentElement.setAttribute("data-text-size", size);
    }
    sizeButtons.forEach((button) => {
      button.setAttribute(
        "aria-pressed",
        String(button.getAttribute("data-text-size") === size)
      );
    });
  };

  let savedTextSize = "normal";
  try {
    savedTextSize = window.localStorage.getItem("voto-text-size") || "normal";
  } catch (_error) {
    savedTextSize = "normal";
  }
  applyTextSize(savedTextSize);

  sizeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const size = button.getAttribute("data-text-size") || "normal";
      applyTextSize(size);
      try {
        window.localStorage.setItem("voto-text-size", size);
      } catch (_error) {
        // Si el navegador bloquea localStorage, el cambio igual funciona.
      }
    });
  });

  // Acceso con tarjeta QR: el código viaja en el fragmento (#) de la URL.
  // Se mueve a un formulario POST y se borra de la barra de direcciones para
  // que no quede en el historial ni en los registros del servidor.
  const quickForm = document.querySelector("[data-quick-access]");
  if (quickForm) {
    const tokenInput = quickForm.querySelector('input[name="token"]');
    const rawToken = window.location.hash.replace(/^#/, "");
    const fallback = document.querySelector("[data-quick-fallback]");
    if (rawToken && tokenInput) {
      tokenInput.value = decodeURIComponent(rawToken);
      if (window.history && window.history.replaceState) {
        window.history.replaceState(
          null,
          "",
          window.location.pathname + window.location.search
        );
      }
      quickForm.submit();
    } else {
      // Sin código en la URL: se muestra la ayuda para escribirlo a mano.
      quickForm.hidden = true;
      if (fallback) fallback.hidden = false;
      const message = document.querySelector("[data-quick-message]");
      if (message) {
        message.textContent =
          "No encontramos un código en el enlace. Puede escribirlo manualmente.";
      }
    }
  }

  // Reinicio automático de la pantalla de mesa después de cada voto.
  document.querySelectorAll("[data-auto-reset]").forEach((element) => {
    const target = element.getAttribute("data-auto-reset");
    if (!target) return;
    const seconds = Number.parseInt(
      element.getAttribute("data-reset-seconds") || "12",
      10
    );
    const label = element.querySelector("[data-reset-countdown]");
    let remaining = Number.isFinite(seconds) ? seconds : 12;

    const tick = () => {
      remaining -= 1;
      if (remaining <= 0) {
        if (label) label.textContent = "0";
        window.location.assign(target);
        return;
      }
      if (label) label.textContent = String(remaining);
      window.setTimeout(tick, 1000);
    };

    if (label) label.textContent = String(remaining);
    window.setTimeout(tick, 1000);
  });
})();
