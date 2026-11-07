(function () {
  var queue = document.querySelector("[data-receipt-queue]");
  if (!queue) return;
  function key() {
    var url = new URL(location.href);
    return (
      "receipt-position:" +
      ["method", "from", "to"]
        .map(function (k) {
          return url.searchParams.get(k) || "";
        })
        .join("|")
    );
  }
  document.addEventListener("submit", function (e) {
    if (!queue.contains(e.target) || e.defaultPrevented) return;
    try {
      sessionStorage.setItem(key(), String(scrollY));
    } catch (_) {}
    var row = e.target.closest("[data-receipt-row]"),
      anchor = e.target.querySelector("[name=return_anchor]");
    if (row && anchor) {
      var rows = Array.from(queue.querySelectorAll("[data-receipt-row]")),
        index = rows.indexOf(row);
      anchor.value = (rows[index + 1] || rows[index - 1] || row).id;
    }
  });
  try {
    var position = sessionStorage.getItem(key());
    if (position !== null) {
      sessionStorage.removeItem(key());
      requestAnimationFrame(function () {
        scrollTo(0, Number(position));
      });
    }
  } catch (_) {}
})();
/* Màn Báo thu 07/11/2026: chọn người không tải lại trang, ô chọn khoản tự tính tổng, ô đếm báo Khớp/Thiếu/Dư, tìm nhanh. */
(function () {
  var queue = document.querySelector("[data-receipt-queue]");
  if (!queue) return;
  function fold(t) {
    return (t || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "d").toLowerCase();
  }
  function fmt(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  }
  function show(key) {
    queue.querySelectorAll("[data-rq-panel]").forEach(function (p) {
      p.hidden = p.getAttribute("data-rq-panel") !== key;
    });
    queue.querySelectorAll("[data-rq-pick]").forEach(function (a) {
      var on = a.getAttribute("data-rq-pick") === key;
      a.classList.toggle("on", on);
      if (on) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
    queue.classList.add("picked");
  }
  queue.addEventListener("click", function (e) {
    var a = e.target.closest("[data-rq-pick]");
    if (a && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      show(a.getAttribute("data-rq-pick"));
      try {
        history.replaceState(null, "", a.href);
      } catch (_) {}
      if (matchMedia("(max-width: 760px)").matches) scrollTo(0, queue.offsetTop - 8);
      return;
    }
    var back = e.target.closest(".rq-back");
    if (back && matchMedia("(max-width: 760px)").matches) {
      e.preventDefault();
      queue.classList.remove("picked");
      try {
        history.replaceState(null, "", back.href);
      } catch (_) {}
    }
  });
  function sync(panel) {
    var form = panel.querySelector("[data-rq-count]");
    if (!form) return;
    var boxes = Array.from(panel.querySelectorAll("[data-rq-cb]")),
      picked = boxes.filter(function (b) {
        return b.checked;
      }),
      need = picked.reduce(function (s, b) {
        return s + Number(b.closest("[data-rq-row]").getAttribute("data-amount"));
      }, 0);
    boxes.forEach(function (b) {
      b.closest("tr").classList.toggle("rq-off", !b.checked);
    });
    var all = panel.querySelector("[data-rq-all]");
    if (all) all.checked = picked.length === boxes.length;
    panel.querySelectorAll("[data-rq-n]").forEach(function (n) {
      n.textContent = picked.length;
    });
    var sum = panel.querySelector("[data-rq-sum]");
    if (sum) sum.textContent = fmt(need);
    var input = form.querySelector("[name=counted_amount]"),
      match = form.querySelector("[data-rq-match]"),
      ok = form.querySelector("[data-rq-ok]"),
      digits = input.value.replace(/\D/g, ""),
      got = digits ? Number(digits) : null;
    if (!picked.length) {
      match.className = "rq-match bad";
      match.textContent = "Chưa chọn khoản nào";
      ok.disabled = true;
    } else if (got === null) {
      match.className = "rq-match";
      match.textContent = "Cần " + fmt(need);
      ok.disabled = true;
    } else if (got === need) {
      match.className = "rq-match ok";
      match.textContent = "Khớp ✓";
      ok.disabled = false;
    } else {
      match.className = "rq-match bad";
      match.textContent = (got < need ? "Thiếu " : "Dư ") + fmt(Math.abs(need - got));
      ok.disabled = true;
    }
  }
  queue.querySelectorAll("[data-rq-panel]").forEach(function (panel) {
    panel.addEventListener("change", function (e) {
      if (e.target.matches("[data-rq-all]"))
        panel.querySelectorAll("[data-rq-cb]").forEach(function (b) {
          b.checked = e.target.checked;
        });
      sync(panel);
    });
    panel.addEventListener("input", function () {
      sync(panel);
    });
    sync(panel);
  });
  var search = document.querySelector("[data-rq-search]");
  if (search) {
    var current = null;
    search.addEventListener("input", function () {
      var q = fold(search.value.trim());
      if (current === null) {
        var on = queue.querySelector("[data-rq-panel]:not([hidden])");
        current = on ? on.getAttribute("data-rq-panel") : "";
      }
      queue.classList.toggle("rq-searching", !!q);
      queue.querySelectorAll("[data-rq-panel]").forEach(function (p) {
        var hits = 0;
        p.querySelectorAll("[data-rq-row]").forEach(function (r) {
          var hit = !q || fold(r.getAttribute("data-search")).indexOf(q) >= 0;
          r.hidden = !hit;
          if (hit) hits++;
        });
        p.hidden = q ? hits === 0 : p.getAttribute("data-rq-panel") !== current;
      });
      if (!q) current = null;
    });
  }
})();
