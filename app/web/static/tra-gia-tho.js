// Bảng giá màn thợ (TUẤN duyệt mẫu M1–M4, 15/11/2026).
(function () {
  function money(n) { return String(Math.round(n || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
  function setUrl(u) { try { history.replaceState(null, '', u); } catch (e) { /* trình duyệt chặn: bỏ qua */ } }
  function fold(s) { return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase(); }
  function live(input, list, url, extra, done) {
    var seq = 0, timer = null;
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var current = ++seq, q = input.value.trim(), params = new URLSearchParams(Object.assign({q: q}, extra ? extra() : {}));
        fetch(url + '?' + params, {credentials: 'same-origin'})
          .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
          .then(function (html) { if (current !== seq) return; list.innerHTML = html; if (done) done(q); })
          .catch(function () {});
      }, 250);
    });
  }

  // B1: gõ model là hiện danh sách khớp (không cần dấu); Enter chọn dòng đầu.
  var mInput = document.querySelector('[data-tg2-find]'), mList = document.querySelector('[data-tg2-list]');
  if (mInput && mList) {
    live(mInput, mList, mInput.dataset.tg2Find);
    mInput.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      var first = mList.querySelector('a');
      if (first) window.location.href = first.href;
    });
  }

  // Phụ phí dùng chung hai tab: ô đã chọn sáng lên.
  function feeTotal(form) {
    var sum = 0;
    form.querySelectorAll('input[name="pp"]').forEach(function (el) {
      el.closest('.tg3-fee').classList.toggle('on', el.checked);
      if (el.checked) sum += +el.dataset.price;
    });
    return sum;
  }

  // B2 + B3 Giá sửa: bấm gói để chọn, chọn cấp; tổng gồm VAT cập nhật ngay, đường dẫn giữ lựa chọn.
  var form = document.querySelector('[data-tg3]');
  if (form && form.querySelector('[data-tg3-total]')) {
    var update = function () {
      var sum = 0, n = 0;
      form.querySelectorAll('[data-tg3-g]').forEach(function (card) {
        var on = card.querySelector('input[name="g"]').checked;
        card.classList.toggle('on', on);
        if (!on) return;
        var lv = card.querySelector('input[type="radio"]:checked');
        if (lv) { sum += +lv.dataset.price; n += 1; }
      });
      var fees = feeTotal(form);
      form.querySelector('[data-tg3-sum]').textContent = n + ' gói · Phụ phí ' + money(fees);
      form.querySelector('[data-tg3-total]').textContent = money(sum + fees);
      var params = new URLSearchParams();
      new FormData(form).forEach(function (v, k) {
        if (k.indexOf('cap_') === 0 && !form.querySelector('input[name="g"][value="' + k.slice(4) + '"]:checked')) return;
        params.append(k, v);
      });
      setUrl('/tra-gia?' + params);
    };
    form.addEventListener('change', update);
    var filter = form.querySelector('[data-tg3-filter]');
    if (filter) filter.addEventListener('input', function () {
      var words = fold(filter.value).split(/\s+/).filter(Boolean);
      form.querySelectorAll('[data-tg3-g]').forEach(function (card) {
        var name = fold(card.dataset.name);
        card.hidden = !words.every(function (w) { return name.indexOf(w) >= 0; });
      });
    });
  }

  // Giá sản phẩm: gõ là hiện hàng khớp; −/+ thêm vào "Hàng đã chọn"; phụ phí; tổng gồm VAT.
  var pInput = document.querySelector('[data-tgsp-find]'), pList = document.querySelector('[data-tgsp-list]');
  var pForm = document.querySelector('[data-tgsp-form]');
  if (!pInput || !pList || !pForm) return;
  var basket = {}, order = [], nhom = '';
  pForm.querySelectorAll('.tgsp-row').forEach(function (row) {
    basket[row.dataset.code] = {name: row.dataset.name, price: +row.dataset.price, qty: +row.dataset.qty};
    order.push(row.dataset.code);
  });
  var esc = function (s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; };
  var qtyCtl = function (n) {
    return '<span class="tgsp-q"><button type="button" class="btn btn-ghost" data-d="-1" aria-label="Bớt một">−</button><b>' + n +
      '</b><button type="button" class="btn btn-ghost" data-d="1" aria-label="Thêm một">+</button></span>';
  };
  function syncCards() {
    pList.querySelectorAll('.tgsp').forEach(function (card) {
      var n = (basket[card.dataset.tgspCode] || {}).qty || 0;
      card.classList.toggle('on', n > 0);
      var b = card.querySelector('[data-tgsp-n]');
      if (b) b.textContent = n;
    });
  }
  function render() {
    var goods = 0, html = '';
    order = order.filter(function (c) { return basket[c] && basket[c].qty > 0; });
    order.forEach(function (c) {
      var it = basket[c];
      goods += it.price * it.qty;
      html += '<div class="tgsp-row" data-code="' + esc(c) + '"><span class="tgsp-rn">' + esc(it.name) + '<small>' + it.qty + ' × ' + money(it.price) +
        '</small></span><b>' + money(it.price * it.qty) + '</b>' + qtyCtl(it.qty) + '</div>';
    });
    pForm.querySelector('[data-tgsp-rows]').innerHTML = html;
    pForm.querySelector('[data-tgsp-picked]').hidden = !order.length;
    var fees = feeTotal(pForm);
    pForm.querySelector('[data-tg3-sum]').textContent = 'Hàng ' + money(goods) + ' · Phụ phí ' + money(fees);
    pForm.querySelector('[data-tg3-total]').textContent = money(goods + fees);
    syncCards();
    var params = new URLSearchParams(), q = pInput.value.trim();
    if (q) params.append('q', q);
    if (nhom) params.append('nhom', nhom);
    order.forEach(function (c) { params.append('sp', c + ':' + basket[c].qty); });
    pForm.querySelectorAll('input[name="pp"]:checked').forEach(function (el) { params.append('pp', el.value); });
    setUrl('/tra-gia/san-pham' + (params.toString() ? '?' + params : ''));
  }
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-d]');
    if (btn) {
      var card = btn.closest('.tgsp'), row = btn.closest('.tgsp-row');
      var code = card ? card.dataset.tgspCode : row && row.dataset.code;
      if (!code) return;
      var it = basket[code];
      if (!it) {
        if (!card) return;
        it = basket[code] = {name: card.dataset.name, price: +card.dataset.price, qty: 0};
      }
      if (order.indexOf(code) < 0) order.push(code);
      it.qty = Math.max(0, Math.min(99, it.qty + (+btn.dataset.d)));
      render();
      return;
    }
    var chip = e.target.closest('.tg2-filters a');
    if (chip && pList.contains(chip)) {
      e.preventDefault();
      nhom = new URL(chip.href).searchParams.get('nhom') || '';
      fetch('/tra-gia/san-pham/tim?' + new URLSearchParams({q: pInput.value.trim(), nhom: nhom}), {credentials: 'same-origin'})
        .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
        .then(function (html) { pList.innerHTML = html; render(); })
        .catch(function () { window.location.href = chip.href; });
    }
  });
  pForm.addEventListener('change', render);
  pForm.querySelector('[data-tgsp-reset]').addEventListener('click', function () {
    basket = {}; order = [];
    pForm.querySelectorAll('input[name="pp"]').forEach(function (el) { el.checked = false; });
    render();
  });
  pInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); pInput.blur(); } });
  nhom = new URLSearchParams(location.search).get('nhom') || '';
  live(pInput, pList, pInput.dataset.tgspFind, null, function () { nhom = ''; render(); });
})();
