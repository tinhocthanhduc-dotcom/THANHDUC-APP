/* Progressive enhancement: fields remain visible and submittable without JS. */
(function () {
  var money = function (value) {
    return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  };
  function locked(block) {
    return (
      !!block.closest("fieldset[disabled]") ||
      !!block.querySelector("[name=customer_code][readonly]")
    );
  }
  function snapshot(block) {
    return Array.from(block.querySelectorAll("input,textarea,select")).map(
      function (i) {
        return [i, i.value, i.checked, i.disabled];
      },
    );
  }
  function label(input) {
    var span = input.closest("label");
    return span ? span.textContent.trim() : input.value;
  }
  function text(block) {
    var details = block.querySelector("[data-picked-detail]");
    if (details.querySelector("[name=customer_code]")) return "";
    var parts = Array.from(
      details.querySelectorAll("input[type=radio]:checked"),
    ).map(label);
    var date = details.querySelector("[name=valid_until]");
    if (date)
      parts.push("Hiệu lực: " + date.value.split("-").reverse().join("/"));
    var term = details.querySelector("[name=payment_term]:checked");
    if (term && term.value === "CONG_NO")
      parts[parts.length - 1] =
        "Công nợ " + details.querySelector("[name=due_days]").value + " ngày";
    if (term && term.value === "DAT_COC")
      parts[parts.length - 1] =
        "Đặt cọc " + details.querySelector("[name=deposit_amount]").value;
    var ap = details.querySelector("[name=appointment_choice]:checked");
    if (ap && ap.value === "GIO")
      parts.push(
        details.querySelector("[name=appointment_at]").value.replace("T", " "),
      );
    return parts.join(" · ");
  }
  function ready(block) {
    var input = block.querySelector("[name=customer_code]");
    if (input)
      return (
        !!input.value && !!block.querySelector("[data-card-title]").textContent
      );
    var fields = Array.from(block.querySelectorAll("[required]")).filter(
      function (i) {
        return !i.disabled;
      },
    );
    return (
      fields.every(function (i) {
        return i.checkValidity();
      }) && !!text(block)
    );
  }
  function collapse(block) {
    if (!ready(block)) return;
    var summary = block.querySelector("[data-picked-summary]");
    if (!block.querySelector("[name=customer_code]"))
      summary.querySelector("[data-picked-text]").textContent = text(block);
    summary.hidden = false;
    block.querySelector("[data-picked-detail]").hidden = true;
    block._pickedSnapshot = snapshot(block);
    block._choices = block
      .closest("form")
      .querySelector("[data-service-choices]")?.innerHTML;
  }
  window.initPicked = function (scope) {
    scope.querySelectorAll("[data-picked]").forEach(function (block) {
      if (block.dataset.pickedBound) return;
      block.dataset.pickedBound = "1";
      if (locked(block)) {
        block
          .querySelectorAll("[data-picked-edit],[data-picked-cancel]")
          .forEach(function (b) {
            b.remove();
          });
        var summary = block.querySelector("[data-picked-summary]");
        if (!summary.querySelector("small")) {
          var reason = document.createElement("small");
          reason.textContent = "Không sửa được ở trạng thái này";
          summary.appendChild(reason);
        }
      }
      if (!block.hasAttribute("data-picked-invalid")) collapse(block);
    });
  };
  document.addEventListener(
    "click",
    function (event) {
      var edit = event.target.closest("[data-picked-edit]"),
        cancel = event.target.closest("[data-picked-cancel]");
      if (edit) {
        var b = edit.closest("[data-picked]");
        b.querySelector("[data-picked-summary]").hidden = true;
        b.querySelector("[data-picked-detail]").hidden = false;
        b.querySelector("[data-picked-cancel]").hidden = false;
      }
      if (cancel) {
        var b = cancel.closest("[data-picked]");
        (b._pickedSnapshot || []).forEach(function (s) {
          s[0].value = s[1];
          s[0].checked = s[2];
          s[0].disabled = s[3];
        });
        if (b._choices !== undefined) {
          var c = b.closest("form").querySelector("[data-service-choices]");
          c.innerHTML = b._choices;
          window.initPicked(c);
        }
        collapse(b);
      }
      var button = event.target.closest(
        "[data-service-customer],[data-quote-pick],[data-order-pick]",
      );
      if (
        button &&
        (button.dataset.kind === "customer" ||
          button.hasAttribute("data-service-customer"))
      ) {
        var block = button.closest("[data-picked]");
        if (!block) return;
        block.querySelector("[name=customer_code]").value =
          button.dataset.serviceCustomer ||
          button.dataset.quotePick ||
          button.dataset.orderPick;
        var card = block.querySelector("[data-customer-card]");
        card.hidden = false;
        card.querySelector("[data-card-title]").textContent =
          (button.dataset.serviceCustomer ||
            button.dataset.quotePick ||
            button.dataset.orderPick) +
          " · " +
          (button.dataset.name || button.dataset.label);
        card.querySelector("[data-card-address]").textContent =
          (button.dataset.phone || "") + " · " + (button.dataset.address || "");
        var debt = card.querySelector("[data-card-debt]");
        if (debt) debt.remove();
        var committed = card.querySelector("[data-card-committed]");
        if (committed) committed.remove();
        if (button.hasAttribute("data-debt")) {
          debt = document.createElement("div");
          debt.dataset.cardDebt = "";
          debt.textContent =
            "Còn nợ " +
            money(button.dataset.debt) +
            " · Hạn mức " +
            money(button.dataset.limit);
          card.appendChild(debt);
          committed = document.createElement("div");
          committed.dataset.cardCommitted = "";
          committed.textContent =
            "Đơn chốt chưa giao " + money(button.dataset.committed || "0");
          card.appendChild(committed);
        }
        var form = block.closest("form"),
          contact = form.querySelector("[name=contact_name]");
        if (contact && !contact.value.trim() && !contact.dataset.userTyped)
          contact.value = button.dataset.contact || "";
        form.dataset.customerAddress = button.dataset.address || "";
        fillAddress(form);
        collapse(block);
      }
    },
    true,
  );
  function fillAddress(form) {
    var address = form.querySelector("[name=delivery_address]"),
      fulfill = form.querySelector("[name=fulfillment]:checked");
    if (
      address &&
      !address.value.trim() &&
      !address.dataset.userTyped &&
      fulfill &&
      ["THO_GIAO", "VAN_CHUYEN"].includes(fulfill.value)
    )
      address.value = form.dataset.customerAddress || "";
  }
  document.addEventListener("input", function (e) {
    if (
      e.target.matches(
        "[name=contact_name],[name=delivery_address],[name=unit_price]",
      )
    )
      e.target.dataset.userTyped = "1";
  });
  document.addEventListener("change", function (e) {
    var b = e.target.closest("[data-picked]");
    // Typing a search is not a committed customer selection.
    if (b && !b.querySelector("[name=customer_code]")) collapse(b);
    if (e.target.name === "fulfillment") fillAddress(e.target.form);
  });
  document.addEventListener(
    "invalid",
    function (e) {
      var block = e.target.closest("[data-picked]");
      if (block) {
        block.querySelector("[data-picked-summary]").hidden = true;
        block.querySelector("[data-picked-detail]").hidden = false;
      }
    },
    true,
  );
  window.initPicked(document);
  document
    .querySelectorAll("[data-customer-card][data-address]")
    .forEach(function (card) {
      card.closest("form").dataset.customerAddress = card.dataset.address;
    });

  var form = document.querySelector("[data-order-form],[data-quote-form]");
  if (!form) return;
  var container = form.querySelector("[data-order-lines],[data-quote-lines]");
  function rows() {
    return Array.from(
      container.querySelectorAll("[data-order-line],[data-quote-line]"),
    );
  }
  function number(value) {
    return /^\d+$/.test(value.replaceAll(".", ""))
      ? BigInt(value.replaceAll(".", ""))
      : null;
  }
  function values(row) {
    var qty = row.querySelector("[name=qty]").value.replace(",", ".");
    var price = number(row.querySelector("[name=unit_price]").value),
      vat = row.querySelector(
        "[data-order-vat]:checked,[data-quote-vat]:checked",
      );
    if (!/^\d+(?:\.\d{1,3})?$/.test(qty) || price === null || !vat) return null;
    var p = qty.split("."),
      q = BigInt(p[0]) * 1000n + BigInt((p[1] || "").padEnd(3, "0"));
    var amount = (q * price + 500n) / 1000n;
    return {
      qty: q,
      amount: amount,
      vat: (amount * BigInt(vat.value) + 50n) / 100n,
    };
  }
  function refresh() {
    var subtotal = 0n,
      tax = 0n,
      complete = true;
    rows().forEach(function (row, index) {
      row.classList.add("line-enhanced");
      var description = row.querySelector("[name=description]").value,
        product = row.querySelector("[name=product_code]").value,
        qty = row.querySelector("[name=qty]").value,
        price = row.querySelector("[name=unit_price]").value;
      var warehouse = row.querySelector("[data-order-warehouse]:checked"),
        kind = row.querySelector("[data-order-kind]:checked"),
        goods = kind && kind.value === "HANG";
      var v = values(row),
        invalid =
          (row.dataset.lineInvalid === "1" && !row.dataset.lineEdited) ||
          (!description && !product) ||
          (goods && (!product || !warehouse || !warehouse.value)) ||
          !/^\d+(?:[.,]\d{1,3})?$/.test(qty) ||
          Number(qty.replace(",", ".")) <= 0 ||
          (price !== "" && number(price) === null);
      var availability = row.querySelector("[data-availability-summary]");
      if (
        form.dataset.orderEditable === "1" &&
        goods &&
        availability &&
        (/Thiếu|Không đủ/i.test(availability.textContent) ||
          (availability.hasAttribute("data-available") &&
            Number(qty.replace(",", ".")) >
              Number(availability.dataset.available)))
      )
        invalid = true;
      row.classList.toggle("line-error", invalid);
      if (invalid) row.classList.add("line-open");
      row.querySelector("[data-line-summary]").textContent =
        index +
        1 +
        " · " +
        (description || product || "Chọn hàng") +
        " · " +
        qty +
        " × " +
        (price || "Giá mã hàng") +
        (goods ? " · " + (warehouse?.value || "Chưa chọn kho") : "") +
        " · ▾";
      row
        .querySelector("[data-line-summary]")
        .setAttribute(
          "aria-expanded",
          row.classList.contains("line-open") ? "true" : "false",
        );
      if (v) {
        subtotal += v.amount;
        tax += v.vat;
      } else complete = false;
    });
    form.querySelector("[data-line-totals]").textContent =
      "Tiền hàng " +
      money(subtotal) +
      " · VAT " +
      money(tax) +
      " · Tổng " +
      money(subtotal + tax) +
      (complete ? "" : " · Chưa đủ dữ liệu") +
      ". Tạm tính, số chính thức tính khi lưu";
  }
  form.addEventListener("click", function (e) {
    var b = e.target.closest("[data-line-summary]");
    if (b) {
      var row = b.closest("[data-order-line],[data-quote-line]"),
        open = !row.classList.contains("line-open");
      rows().forEach(function (other) {
        if (!other.classList.contains("line-error"))
          other.classList.remove("line-open");
      });
      row.classList.toggle(
        "line-open",
        open || row.classList.contains("line-error"),
      );
    }
    refresh();
  });
  function edited(e) {
    var row = e.target.closest("[data-order-line],[data-quote-line]");
    if (row) row.dataset.lineEdited = "1";
    refresh();
  }
  form.addEventListener("input", edited);
  form.addEventListener("change", edited);
  new MutationObserver(function (mutations) {
    var added = mutations
      .flatMap(function (m) {
        return Array.from(m.addedNodes);
      })
      .filter(function (node) {
        return (
          node.nodeType === 1 &&
          node.matches("[data-order-line],[data-quote-line]")
        );
      });
    if (added.length)
      rows().forEach(function (row) {
        if (!added.includes(row) && !row.classList.contains("line-error"))
          row.classList.remove("line-open");
      });
    refresh();
  }).observe(container, { childList: true });
  form.addEventListener("keydown", function (e) {
    if (
      e.target.matches("[data-line-summary]") &&
      (e.key === "Enter" || e.key === " ")
    ) {
      e.preventDefault();
      e.target.click();
    }
  });
  form.addEventListener("submit", function () {
    rows().forEach(function (row) {
      if (row.querySelector(":invalid"))
        row.classList.add("line-open", "line-error");
    });
  });
  form.addEventListener(
    "invalid",
    function (e) {
      var row = e.target.closest("[data-order-line],[data-quote-line]");
      if (row) row.classList.add("line-open", "line-error");
    },
    true,
  );
  refresh();
})();

/* "+ Tạo mới khách hàng": mang theo chữ đang gõ ở ô tìm khách sang form tạo khách. */
document.addEventListener("click", function (e) {
  var link = e.target.closest && e.target.closest("[data-new-customer]");
  if (!link) return;
  var box = link.closest("[data-picker]") || link.parentElement;
  var input = box && box.querySelector("[name=customer_code]");
  var typed = input ? input.value.trim() : "";
  if (!typed || /^KTD-\d+$/i.test(typed)) return;
  e.preventDefault();
  var url = new URL(link.href, location.href);
  url.searchParams.set("q", typed);
  location.href = url.toString();
});
