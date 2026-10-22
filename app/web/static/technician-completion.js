(function () {
  const form = document.querySelector("[data-tech-completion]");
  const screen = document.querySelector("[data-technician-ticket]");
  const format = (n) => n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const money = (v) => {
    const raw = v.replace(/\./g, "").trim();
    return /^[0-9]{1,18}$/.test(raw) ? BigInt(raw) : 0n;
  };
  function actual() {
    const gross = money(form.elements.customer_amount.value),
      denom = 100n + BigInt(form.dataset.vat),
      center = (gross * 100n + denom / 2n) / denom;
    let best = null;
    for (let net = center - 2n; net <= center + 2n; net++) {
      if (net < 0n) continue;
      const tax = (net * BigInt(form.dataset.vat) + 50n) / 100n,
        total = net + tax,
        error = total > gross ? total - gross : gross - total,
        distance = net > center ? net - center : center - net;
      if (
        !best ||
        error < best.error ||
        (error === best.error && distance < best.distance)
      )
        best = { total, error, distance };
    }
    return best ? best.total : 0n;
  }
  function update() {
    if (!form) return;
    const mode =
        screen.querySelector("[name=collection_mode]:checked")?.value || "",
      free = mode === "FREE",
      total = actual();
    screen.querySelector("[data-customer-charge]").hidden = free;
    screen.querySelector("[data-tech-partial]").hidden = mode !== "PARTIAL";
    screen.querySelector("[data-tech-free]").hidden = !free;
    screen.querySelector("[data-tech-method]").hidden = ![
      "FULL",
      "PARTIAL",
    ].includes(mode);
    screen.querySelector("[data-tech-actual]").textContent = format(total);
    // Đơn giao cùng chuyến: hiện tổng thu thực tế = phiếu dịch vụ + đơn giao kèm.
    const trip = screen.querySelector("[data-trip-money]"), tripSum = screen.querySelector("[data-trip-total-sum]");
    if (trip && tripSum) tripSum.textContent = format((free ? 0n : total) + BigInt(trip.dataset.orderDue || "0"));
    form.elements.free_reason.required = free;
    form.elements.partial_amount.required = mode === "PARTIAL";
    let ready = true;
    document.querySelectorAll("[data-check]").forEach((check) => {
      let ok = check.dataset.ok === "1";
      if (check.dataset.check === "billing") {
        ok = free
          ? form.elements.free_reason.value.trim().length >= 3
          : total > 0n;
        if (mode === "PARTIAL") {
          const part = money(form.elements.partial_amount.value);
          ok = ok && part > 0n && part < total;
        }
        check.dataset.label = ok
          ? "Đã nhập tiền thu khách hoặc chọn Miễn phí"
          : "Chưa nhập tiền thu khách hoặc chọn Miễn phí";
      }
      if (check.dataset.check === "report")
        ok = !!document.querySelector("[name=repair_result]:checked");
      if (check.dataset.check === "photo")
        ok =
          document.querySelector("[data-photo-tag=SAU] [data-saved-photo]") !==
            null &&
          !document.querySelector("[data-photo-tag=SAU] [data-uploading]");
      if (check.dataset.check === "warehouses")
        ok = Array.from(screen.querySelectorAll("[name=material_qty]")).every(
          (input) =>
            Number(input.value.replace(",", ".")) >= 0 &&
            Number(input.value.replace(",", ".")) <= Number(input.dataset.held),
        );
      check.dataset.ok = ok ? "1" : "0";
      check.textContent = (ok ? "✓ " : "✗ ") + check.dataset.label;
      ready = ready && ok;
    });
    const button = document.querySelector("[data-tho-primary]");
    if (button) button.disabled = !ready;
    document.querySelector("#completion-checks .alert").hidden = ready;
  }
  if (form) {
    screen.addEventListener("input", update);
    screen.addEventListener("change", update);
    screen.addEventListener("click", (e) => {
      const suggestion = e.target.closest("[data-fee-suggestion]"),
        step = e.target.closest("[data-qty-step]");
      if (suggestion)
        form.elements.customer_amount.value = format(
          BigInt(suggestion.dataset.feeSuggestion),
        );
      if (step) {
        const input = step
          .closest("[data-tech-material]")
          .querySelector("[name=material_qty]");
        input.value = String(
          Math.min(
            Number(input.dataset.held),
            Math.max(
              0,
              Number(input.value.replace(",", ".")) +
                Number(step.dataset.qtyStep),
            ),
          ),
        );
      }
      if (e.target.closest("[data-collection-clear]"))
        screen
          .querySelectorAll("[name=collection_mode]")
          .forEach((input) => (input.checked = false));
      update();
    });
    // UX5a: mã vừa tìm thêm bắt đầu với số 1.
    screen.addEventListener("item-picked", (e) => {
      const input = e.target.querySelector("[name=material_qty]");
      if (input && !(Number(input.value.replace(",", ".")) > 0)) input.value = "1";
      update();
    });
    document.addEventListener("tech-photo-change", update);
    update();
  }
  document.querySelectorAll("[data-failure-options]").forEach((box) => {
    function toggle() {
      const transfer =
        box.querySelector("[name=failure_choice]:checked").value === "TRANSFER";
      box.querySelector("[data-failure-transfer]").hidden = !transfer;
      box.querySelector("[data-failure-close]").hidden = transfer;
    }
    box.addEventListener("change", toggle);
    toggle();
  });
  document.querySelectorAll(".tech-other-list>details").forEach((details) =>
    details.addEventListener("toggle", () => {
      if (details.open)
        document
          .querySelectorAll(".tech-other-list>details")
          .forEach((other) => {
            if (other !== details) other.open = false;
          });
    }),
  );
})();
