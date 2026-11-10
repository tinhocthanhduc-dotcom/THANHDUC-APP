// Đơn giá chưa VAT / đã gồm VAT trên báo giá và đơn bán (chỉ giao diện; máy chủ tính tiền khi lưu).
// - Giá niêm yết và "Giá lần trước" luôn là giá chưa VAT; chế độ "Đã gồm VAT" thì cộng VAT của dòng.
// - Đổi chế độ: quy đổi đơn giá các dòng đang có để tổng gần như giữ nguyên.
(function () {
  var box = document.querySelector("[data-price-mode]"), form = box && box.closest("form");
  if (!form) return;
  function digits(v) { var s = String(v || "").replace(/\./g, "").trim(); return /^\d+$/.test(s) ? BigInt(s) : null; }
  function dots(n) { return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, "."); }
  function half(num, den) { return (num * 2n + den) / (2n * den); }
  function included() { var r = form.querySelector("[name=price_includes_vat]:checked"); return !!r && r.value === "1"; }
  function rate(row) {
    var r = row && row.querySelector("input[type=radio][name^=vat_]:checked");
    return r ? BigInt(r.value) : null;
  }
  function lineRows() { return [].slice.call(form.querySelectorAll("[data-quote-line],[data-order-line]")); }
  // Giá chưa VAT (niêm yết, lần trước) → giá hiện ở ô Đơn giá theo chế độ đang chọn.
  window.tdShownPrice = function (row, raw) {
    var n = digits(raw);
    if (n === null) return raw || "";
    var r = rate(row);
    if (included() && r !== null) n = half(n * (100n + r), 100n);
    return dots(n);
  };
  function heads() {
    var on = included();
    form.querySelectorAll("[data-price-head]").forEach(function (el) { el.textContent = on ? "Đơn giá gồm VAT" : "Đơn giá chưa VAT"; });
    form.querySelectorAll("[data-price-label]").forEach(function (el) { el.textContent = on ? "Đơn giá gồm VAT (đồng)" : "Đơn giá chưa VAT (đồng)"; });
  }
  heads();
  var last = included();
  form.addEventListener("change", function (e) {
    if (e.target.name !== "price_includes_vat") return;
    var on = included();
    if (on === last) return;
    last = on;
    var changed = 0, rates = {};
    lineRows().forEach(function (row) {
      var input = row.querySelector("[name=unit_price]"), n = digits(input && input.value), r = rate(row);
      if (r !== null) rates[r] = 1;
      if (n === null || r === null) return;
      input.value = dots(on ? half(n * (100n + r), 100n) : half(n * 100n, 100n + r));
      changed++;
    });
    // Giảm giá toàn đơn nhập theo cùng kiểu giá: quy đổi khi cả đơn cùng một mức VAT.
    var disc = form.querySelector("[name=discount_amount]"), d = digits(disc && disc.value), keys = Object.keys(rates), note = "";
    if (d && d > 0n) {
      if (keys.length === 1) { var r1 = BigInt(keys[0]); disc.value = dots(on ? half(d * (100n + r1), 100n) : half(d * 100n, 100n + r1)); }
      else note = " Kiểm tra lại số Giảm giá.";
    }
    heads();
    var any = lineRows()[0];
    if (any) { var q = any.querySelector("[name=qty]"); if (q) q.dispatchEvent(new Event("input", { bubbles: true })); }
    if (window.tdToast && changed) window.tdToast("Đã đổi đơn giá " + changed + " dòng sang giá " + (on ? "gồm VAT" : "chưa VAT") + ". Tổng giữ nguyên." + note);
  });
})();
