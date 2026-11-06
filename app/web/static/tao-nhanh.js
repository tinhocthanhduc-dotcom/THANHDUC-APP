/* Tạo mới nhanh (TUẤN duyệt 06/11/2026): "+ Tạo mới" khách / mã hàng / NCC mở cửa sổ ngay trên màn đang soạn,
   lưu xong tự chọn vào ô đang tìm. Không chuyển trang nên phiếu đang soạn giữ nguyên. */
(function () {
  var allowed = (document.body.getAttribute("data-qn") || "").split(" ").filter(Boolean);
  var SOURCES = {
    khach: ["/don-ban/tim-khach", "/bao-gia/tim-khach", "/dich-vu/tim-khach", "/quay-sua/tim-khach",
      "/thiet-bi/tim-khach", "/phieu-kho/tim-khach", "/thu-chi/tim/khach"],
    hang: ["/don-ban/tim-hang", "/bao-gia/tim-hang", "/don-mua/tim-hang", "/phieu-kho/tim-hang",
      "/dich-vu/tim-vat-tu", "/thiet-bi/tim-hang"],
    ncc: ["/don-mua/tim-ncc", "/phieu-kho/tim-ncc", "/cho-xu-ly/tim-ncc", "/thu-chi/tim/nha-cung-cap"],
  };
  var LABELS = { khach: "khách hàng", hang: "mã hàng", ncc: "nhà cung cấp" };

  function kindOf(input) {
    var src = input.getAttribute("data-source") || "";
    for (var k in SOURCES) if (SOURCES[k].indexOf(src) >= 0) return k;
    return "";
  }
  function liveTarget(input) {
    var scope = input.closest("[data-stock-line], [data-picker]") || document;
    return scope.querySelector(input.dataset.live);
  }
  // Ô tìm đang gõ mã (KTD-…, TD-…, NCC-…) thì không mang sang cửa sổ.
  function typed(input) {
    var v = (input && input.value || "").trim();
    return /^(KTD|TD|NCC)-\d+$/i.test(v) ? "" : v;
  }

  // ---- Nút "+ Tạo mới" ở cuối danh sách kết quả tìm ----
  document.addEventListener("live-loaded", function (e) {
    var input = e.target, kind = kindOf(input);
    if (!kind || input.readOnly || input.disabled || !input.value.trim()) return;
    var target = liveTarget(input);
    if (!target || target.querySelector("[data-qn-add], .qn-ask")) return;
    var box = input.closest("[data-picker]");
    if (box && box.querySelector("[data-new-customer]")) return; // màn đã có nút cố định
    var hasRows = !!target.querySelector("button");
    if (allowed.indexOf(kind) >= 0) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "qn-add"; b.setAttribute("data-qn-add", kind);
      b.textContent = "+ Tạo mới " + LABELS[kind];
      b._qnInput = input;
      target.appendChild(b);
    } else if (kind === "hang" && !hasRows) {
      var p = document.createElement("p");
      p.className = "hint qn-ask"; p.textContent = "Không thấy mã? Báo kho tạo mã mới.";
      target.appendChild(p);
    }
  });

  // Ô "Tìm hàng để thêm" ở Đơn bán, Báo giá, Đơn mua, Phiếu kho: danh sách tự vẽ, không qua bindLive.
  var FINDS = [["[data-browse]", "[data-browse-q]", "[data-browse-list]", "[data-browse-add]"],
    ["[data-po-find]", "[data-po-q]", "[data-po-list]", "[data-po-add]"],
    ["[data-sk-find]", "[data-sk-q]", "[data-sk-list]", "[data-sk-add]"]];
  FINDS.forEach(function (f) {
    var box = document.querySelector(f[0]), q = box && box.querySelector(f[1]), list = box && box.querySelector(f[2]);
    if (!q || !list) return;
    new MutationObserver(function () {
      if (!q.value.trim() || list.querySelector("[data-qn-add], .qn-ask")) return;
      if (allowed.indexOf("hang") >= 0) {
        var b = document.createElement("button");
        b.type = "button"; b.className = "qn-add qn-add-find"; b.setAttribute("data-qn-add", "hang");
        b.textContent = "+ Tạo mới mã hàng";
        b._qnInput = q; b._qnFind = { list: list, add: f[3] };
        list.appendChild(b);
      } else if (!list.querySelector(f[3])) {
        var p = document.createElement("p");
        p.className = "hint qn-ask"; p.textContent = "Không thấy mã? Báo kho tạo mã mới.";
        list.appendChild(p);
      }
    }).observe(list, { childList: true });
  });

  document.addEventListener("click", function (e) {
    var add = e.target.closest && e.target.closest("[data-qn-add]");
    if (add) { e.preventDefault(); e.stopPropagation(); open(add.getAttribute("data-qn-add"), { input: add._qnInput, find: add._qnFind }); return; }
    var line = e.target.closest && e.target.closest("[data-qn-new-line]");
    if (line && allowed.indexOf("hang") >= 0) {
      // Nút cố định cạnh "+ Dòng hàng tìm theo mã": lưu xong thêm dòng qua ô Tìm hàng của màn.
      e.preventDefault();
      var form = document;
      var f = FINDS.filter(function (x) { return form.querySelector(x[0]) && form.querySelector(x[0]).querySelector(x[1]); })[0];
      if (f) {
        var fb = form.querySelector(f[0]);
        open("hang", { input: fb.querySelector(f[1]), find: { list: fb.querySelector(f[2]), add: f[3] }, q: "" });
      }
      return;
    }
    var link = e.target.closest && e.target.closest("[data-new-customer]");
    if (link && allowed.indexOf("khach") >= 0) {
      e.preventDefault();
      var box = link.closest("[data-picker]") || link.parentElement;
      var input = box && (box.querySelector("[name=customer_code]") || box.querySelector("input[data-live]"));
      open("khach", { input: input });
      return;
    }
    var pick = e.target.closest && e.target.closest("[data-qn-pick]");
    if (pick && allowed.indexOf(pick.getAttribute("data-qn-pick")) >= 0) {
      e.preventDefault();
      var root = pick.closest("[data-item-picker]");
      open(pick.getAttribute("data-qn-pick"), { pickRoot: root, q: root && (root.querySelector("[data-pick-search]") || {}).value });
    }
  });

  // ---- Cửa sổ ----
  var dialog = null, ctx = null;
  function ensureDialog() {
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.className = "qn-dialog";
    dialog.setAttribute("aria-label", "Tạo mới nhanh");
    document.body.appendChild(dialog);
    dialog.addEventListener("click", onDialogClick);
    dialog.addEventListener("submit", onSubmit);
    dialog.addEventListener("focusout", onDupCheck);
    dialog.addEventListener("change", onChange);
    dialog.addEventListener("close", function () { dialog.innerHTML = ""; var back = ctx && ctx.input; ctx = null; if (back && back.isConnected) back.focus(); });
    return dialog;
  }
  function forLabel() {
    var h = document.querySelector("main h1");
    return h ? "Đang tạo cho " + h.textContent.trim() : "";
  }
  function show(html) {
    dialog.innerHTML = html;
    var kicker = dialog.querySelector("[data-qn-for]");
    if (kicker) kicker.textContent = forLabel();
    dialog.querySelectorAll("[data-money]").forEach(function (el) { if (window.bindMoney) window.bindMoney(el); });
    var first = dialog.querySelector(".field-invalid input, [aria-invalid=true]") ||
      Array.prototype.filter.call(dialog.querySelectorAll(".qn-body input[required]:not([type=checkbox]), .qn-body input[name=name]"), function (el) { return !el.value; })[0] ||
      dialog.querySelector(".qn-body input[name=name]");
    if (first) first.focus();
  }
  function open(kind, opts) {
    ensureDialog();
    ctx = { kind: kind, input: opts.input || null, find: opts.find || null, pickRoot: opts.pickRoot || null, warrantyTouched: false };
    var q = opts.q !== undefined ? (opts.q || "") : typed(ctx.input);
    dialog.innerHTML = '<p class="qn-loading">Đang mở…</p>';
    if (!dialog.open) dialog.showModal();
    fetch("/tao-nhanh/" + kind + "?" + new URLSearchParams({ q: q.trim() }), { credentials: "same-origin" })
      .then(function (r) { return r.text().then(function (t) { return { ok: r.ok, html: t }; }); })
      .then(function (res) {
        if (!res.ok) { dialog.innerHTML = '<p class="alert bad">Không mở được cửa sổ tạo mới. Tải lại trang rồi thử lại.</p><div class="qn-foot"><button type="button" class="btn btn-ghost" data-qn-close>Đóng</button></div>'; return; }
        show(res.html);
      })
      .catch(function () { dialog.innerHTML = '<p class="alert bad">Mất kết nối, hãy thử lại.</p><div class="qn-foot"><button type="button" class="btn btn-ghost" data-qn-close>Đóng</button></div>'; });
  }
  function close() { if (dialog && dialog.open) dialog.close(); }

  function onDialogClick(e) {
    if (e.target.closest("[data-qn-close]")) { e.preventDefault(); close(); return; }
    var use = e.target.closest("[data-qn-use], [data-use-customer]");
    if (use) {
      var code = use.getAttribute("data-qn-use") || use.getAttribute("data-code");
      if (!code) return;
      e.preventDefault();
      done({ code: code });
    }
  }
  function onChange(e) {
    // Giám đốc: chọn nhóm "Máy…" thì điền sẵn bảo hành 12 tháng + theo dõi serial, nếu chưa tự chọn.
    if (e.target.matches("[data-qn-warranty] input")) { ctx.warrantyTouched = true; return; }
    if (!e.target.matches("[data-qn-group]")) return;
    var box = dialog.querySelector("[data-qn-warranty]"), serial = dialog.querySelector("[data-qn-serial]");
    if (!box || ctx.warrantyTouched) return;
    var may = /^máy/i.test(e.target.value.trim());
    var radio = box.querySelector('input[value="' + (may ? "12" : "0") + '"]');
    if (radio) radio.checked = true;
    if (serial) serial.checked = may;
  }
  var dupTimer = null;
  function onDupCheck(e) {
    if (!e.target.matches("[data-qn-dup]")) return;
    clearTimeout(dupTimer);
    dupTimer = setTimeout(function () {
      var form = dialog.querySelector("[data-qn-form]"), out = dialog.querySelector("[data-qn-dup-result]");
      if (!form || !out) return;
      var phone = form.querySelector('[data-qn-dup="phone"]').value.trim(), tax = form.querySelector('[data-qn-dup="tax"]').value.trim();
      var kind = form.querySelector("[name=kind]:checked");
      if (!phone && !tax) { out.innerHTML = ""; return; }
      fetch("/khach-hang/kiem-trung?" + new URLSearchParams({ phone: phone, tax: tax, kind: kind ? kind.value : "CA_NHAN" }), { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.text() : ""; })
        .then(function (html) { if (out.isConnected) out.innerHTML = html; })
        .catch(function () {});
    }, 300);
  }
  function onSubmit(e) {
    var form = e.target.closest("[data-qn-form]");
    if (!form) return;
    e.preventDefault(); // không để khóa bấm chung của trang chặn cửa sổ
    var save = form.querySelector("[data-qn-save]");
    if (save) { save.disabled = true; save.textContent = "Đang lưu…"; }
    fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        var json = (r.headers.get("content-type") || "").indexOf("json") >= 0;
        return (json ? r.json() : r.text()).then(function (body) { return { ok: r.ok, json: json, body: body }; });
      })
      .then(function (res) {
        if (res.ok && res.json) { done(res.body); return; }
        if (typeof res.body === "string" && res.body.indexOf("data-qn-form") >= 0) { show(res.body); return; }
        throw new Error();
      })
      .catch(function () {
        if (save) { save.disabled = false; save.textContent = "Lưu lại"; }
        var body = dialog.querySelector(".qn-body");
        if (body && !body.querySelector(".qn-net")) body.insertAdjacentHTML("afterbegin", '<p class="alert bad qn-net" role="alert">Chưa lưu được: mất kết nối hoặc trang đã cũ. Thử lại, hoặc tải lại trang.</p>');
      });
  }

  // ---- Lưu xong: chọn vào ô đang tìm ----
  function done(rec) {
    var c = ctx;
    close();
    if (!c) return;
    if (c.pickRoot) { addPickRow(c.pickRoot, rec); toast(rec, c.kind); return; }
    var input = c.input;
    if (!input || !input.isConnected) { toast(rec, c.kind); return; }
    if (c.find) { addFromFind(input, c.find, rec); toast(rec, c.kind); return; }
    var target = liveTarget(input);
    input.value = rec.code;
    var once = function () {
      input.removeEventListener("live-loaded", once);
      var hit = target && Array.prototype.filter.call(target.querySelectorAll("button"), function (b) {
        return !b.hasAttribute("data-qn-add") && Array.prototype.some.call(b.attributes, function (a) {
          return a.name.indexOf("data-") === 0 && a.value.toUpperCase() === String(rec.code).toUpperCase();
        });
      })[0];
      if (hit) hit.click();
    };
    input.addEventListener("live-loaded", once);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    toast(rec, c.kind);
  }
  // Gõ mã mới vào ô tìm, chờ danh sách tải lại rồi bấm "Thêm" đúng mã đó (như người dùng tự bấm).
  function addFromFind(input, find, rec) {
    var code = String(rec.code).toUpperCase(), stop = null;
    var hit = function () {
      return Array.prototype.filter.call(find.list.querySelectorAll(find.add), function (b) { return String(b.getAttribute("data-code") || "").toUpperCase() === code; })[0];
    };
    var obs = new MutationObserver(function () {
      var b = hit();
      if (!b) return;
      obs.disconnect(); clearTimeout(stop);
      b.click();
    });
    obs.observe(find.list, { childList: true, subtree: true });
    stop = setTimeout(function () { obs.disconnect(); }, 8000);
    input.value = rec.code;
    input.focus();
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
  function toast(rec, kind) {
    var text = (kind === "khach" ? "Đã tạo khách " : kind === "ncc" ? "Đã tạo nhà cung cấp " : "Đã tạo mã ") + (rec.label || rec.code) + ".";
    if (window.tdToast) window.tdToast(text);
  }
  // Màn sản xuất: danh sách mã có sẵn trong trang, thêm một dòng mới theo mẫu dòng đầu rồi chọn luôn.
  function addPickRow(root, rec) {
    var tpl = root && root.querySelector("[data-pick-row]");
    if (!tpl) return;
    var rows = root.querySelectorAll("[data-pick-row]"), last = rows[rows.length - 1];
    var oldName = tpl.getAttribute("data-pick-name") || "";
    var row = tpl.cloneNode(true);
    var w = rec.warranty_months || 0;
    row.setAttribute("data-pick-text", rec.code + " " + rec.name);
    row.setAttribute("data-pick-name", rec.name);
    row.setAttribute("data-pick-meta", rec.code + (w ? " · bảo hành " + w + " tháng" : ""));
    var nameCell = row.querySelector(".name");
    if (nameCell) {
      var b = nameCell.querySelector("b"), sub = nameCell.querySelector(".lt-sub");
      if (b) b.textContent = rec.name;
      if (sub) sub.textContent = rec.code;
    }
    row.querySelectorAll("input[type=hidden]").forEach(function (el) { if (el.value === (tpl.querySelector("input[type=hidden]") || {}).value) el.value = rec.code; });
    var wcell = row.cells && row.cells[1];
    if (wcell && wcell.querySelector(".sx-ph")) {
      var txt = w ? w + " tháng" + (rec.track_serial ? " · serial khi nhận" : "") : rec.track_serial ? "serial khi nhận" : "–";
      wcell.innerHTML = '<span class="sx-ph">Bảo hành </span>';
      wcell.appendChild(document.createTextNode(txt));
    }
    row.querySelectorAll("[aria-label]").forEach(function (el) { if (oldName) el.setAttribute("aria-label", el.getAttribute("aria-label").split(oldName).join(rec.name)); });
    row.querySelectorAll("input[data-sx-plan]").forEach(function (el) { el.value = "10"; });
    row.querySelectorAll("[data-bom-short]").forEach(function (el) { el.hidden = true; });
    row.hidden = true;
    row.setAttribute("data-pick-extra", "");
    last.parentNode.insertBefore(row, last.nextSibling);
    var search = root.querySelector("[data-pick-search]");
    if (search) search.value = "";
    row.hidden = false;
    row.dispatchEvent(new CustomEvent("item-picked", { bubbles: true }));
    root.dispatchEvent(new CustomEvent("item-picker-refresh"));
  }
})();
