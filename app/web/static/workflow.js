/* Early feedback only: all decisions are repeated inside the server transaction. */
(function () {
  var form = document.querySelector("[data-workflow-form]"),
    action = document.querySelector("[data-workflow-action]");
  if (!form || !action) return;
  var primary = action.querySelector("[data-next-primary]"),
    checks = action.querySelector("[data-workflow-checks]");
  // Live draft checks must never disable delivery or collection on a read-only order.
  if (primary.getAttribute('form') !== form.id || primary.name !== 'then') return;
  var timer,
    sequence = 0;
  function state() {
    var missing = Array.from(
      checks.querySelectorAll('[data-blocking="1"][data-ok="0"]'),
    );
    primary.disabled = missing.length > 0;
    var hint = action.querySelector("[data-primary-missing]");
    hint.hidden = !missing.length;
    hint.textContent =
      "Còn thiếu: " +
      missing
        .map(function (c) {
          return c.textContent.replace(/^[✓✗]\s*/, "");
        })
        .join(" · ");
  }
  function set(key, ok) {
    var item = checks.querySelector('[data-condition="' + key + '"]');
    if (item) {
      item.dataset.ok = ok ? "1" : "0";
      item.textContent =
        (ok ? "✓" : "✗") + " " + item.textContent.replace(/^[✓✗]\s*/, "");
    }
  }
  function update() {
    if (form.dataset.workflowForm === "quote") {
      set(
        "customer",
        !!form.querySelector("[name=customer_code]").value.trim(),
      );
      set("lines", form.querySelectorAll("[data-quote-line]").length > 0);
      set(
        "valid-until",
        form.querySelector("[name=valid_until]").value >= form.dataset.today,
      );
      state();
      return;
    }
    var current = ++sequence;
    clearTimeout(timer);
    primary.disabled = true;
    timer = setTimeout(function () {
      var params = new URLSearchParams(new FormData(form));
      params.delete("csrf");
      params.delete("form_key");
      fetch("/don-ban/dieu-kien?" + params, { credentials: "same-origin" })
        .then(function (r) {
          if (!r.ok) throw new Error();
          return r.text();
        })
        .then(function (html) {
          if (current !== sequence) return;
          var doc = document.createElement("div");
          doc.innerHTML = html;
          checks.replaceChildren(
            ...doc.querySelector("[data-workflow-checks]").childNodes,
          );
          state();
        })
        .catch(function () {
          if (current !== sequence) return;
          primary.disabled = false;
          var hint = action.querySelector("[data-primary-missing]");
          hint.hidden = false;
          hint.textContent =
            "Chưa tải được điều kiện. Máy chủ sẽ kiểm tra khi lưu và chốt.";
        });
    }, 200);
  }
  form.addEventListener("input", update);
  form.addEventListener("change", update);
  form.addEventListener("click", function (event) {
    // Nút Lưu và chốt nằm trong form: không khóa nút ngay lúc bấm, nếu không trình duyệt hủy lượt gửi.
    if (event.target.closest("button[type=submit], button:not([type]), [data-next-primary]")) return;
    queueMicrotask(update);
  });
  var lines = form.querySelector("[data-quote-lines],[data-order-lines]");
  if (lines) new MutationObserver(update).observe(lines, { childList: true });
  update();
})();
