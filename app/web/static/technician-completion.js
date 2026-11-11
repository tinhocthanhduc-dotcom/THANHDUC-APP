(function () {
  const form = document.querySelector("[data-tech-completion]");
  const screen = document.querySelector("[data-technician-ticket]");
  const format = (n) => n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const money = (v) => {
    const raw = v.replace(/\./g, "").trim();
    return /^[0-9]{1,18}$/.test(raw) ? BigInt(raw) : 0n;
  };
  // Giá thợ gõ là giá đã gồm VAT: phải thu đúng số đó, VAT = tổng − tiền trước thuế (như báo giá gồm VAT).
  function actual() {
    return money(form.elements.customer_amount.value);
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
        if (form.dataset.workshop === "1" && !["FULL", "FREE"].includes(mode)) ok = false;
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
      update();
    });
    // UX5a: mã vừa tìm thêm bắt đầu với số 1.
    screen.addEventListener("item-picked", (e) => {
      const input = e.target.querySelector("[name=material_qty]");
      if (input && !(Number(input.value.replace(",", ".")) > 0)) input.value = "1";
      update();
    });
    document.addEventListener("tech-photo-change", update);
    // Lỗi chạy thử 25/10: chọn Loại dịch vụ lưu tại chỗ (service-kind.js) nhưng mục "Đã chọn loại dịch vụ" vẫn ✗, khóa nút Hoàn thành.
    document.addEventListener("service-kind-saved", (e) => {
      const check = document.querySelector("[data-check=kind]");
      if (check) check.dataset.ok = e.detail && e.detail.ok === false ? "0" : "1";
      update();
    });
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
