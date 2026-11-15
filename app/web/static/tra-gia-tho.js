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
        card.hidden = !on;
        var opt = form.querySelector('[data-tg3-pick] option[value="' + card.dataset.sid + '"]');
        if (opt) opt.disabled = on;
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
    // Ô xổ xuống chọn gói (TUẤN 15/11): chọn là hiện thẻ gói với ô cấp; bỏ tick là ẩn thẻ, gói quay lại ô xổ xuống.
    var pick = form.querySelector('[data-tg3-pick]');
    if (pick) pick.addEventListener('change', function (e) {
      e.stopPropagation();
      var card = form.querySelector('[data-tg3-g][data-sid="' + pick.value + '"]');
      pick.value = '';
      if (!card) return;
      card.querySelector('input[name="g"]').checked = true;
      update();
      card.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    });
  }

  // Giá sản phẩm (TUẤN 15/11): gõ là hiện danh sách xổ xuống; bấm dòng thêm vào "Hàng đã chọn"; −/+; phụ phí; tổng gồm VAT.
  var pInput = document.querySelector('[data-tgsp-find]'), pList = document.querySelector('[data-tgsp-list]');
  var pForm = document.querySelector('[data-tgsp-form]');
  if (!pInput || !pList || !pForm) return;
  var basket = {}, order = [];
  pForm.querySelectorAll('.tgsp-row').forEach(function (row) {
    basket[row.dataset.code] = {name: row.dataset.name, price: +row.dataset.price, qty: +row.dataset.qty};
    order.push(row.dataset.code);
  });
  var esc = function (s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; };
  var qtyCtl = function (n) {
    return '<span class="tgsp-q"><button type="button" class="btn btn-ghost" data-d="-1" aria-label="Bớt một">−</button><b>' + n +
      '</b><button type="button" class="btn btn-ghost" data-d="1" aria-label="Thêm một">+</button></span>';
  };
  function markOptions() {
    pList.querySelectorAll('[data-tgsp-add]').forEach(function (opt) {
      var n = (basket[opt.dataset.tgspCode] || {}).qty || 0, em = opt.querySelector('[data-tgsp-n]');
      if (em) em.textContent = n ? ' · Đã chọn ' + n : '';
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
    var hint = document.querySelector('[data-tgsp-empty]');
    if (hint) hint.hidden = !!order.length;
    var fees = feeTotal(pForm);
    pForm.querySelector('[data-tg3-sum]').textContent = 'Hàng ' + money(goods) + ' · Phụ phí ' + money(fees);
    pForm.querySelector('[data-tg3-total]').textContent = money(goods + fees);
    markOptions();
    var params = new URLSearchParams();
    order.forEach(function (c) { params.append('sp', c + ':' + basket[c].qty); });
    pForm.querySelectorAll('input[name="pp"]:checked').forEach(function (el) { params.append('pp', el.value); });
    setUrl('/tra-gia/san-pham' + (params.toString() ? '?' + params : ''));
  }
  function add(code, name, price, d) {
    var it = basket[code];
    if (!it) {
      if (!name) return;
      it = basket[code] = {name: name, price: price, qty: 0};
    }
    if (order.indexOf(code) < 0) order.push(code);
    it.qty = Math.max(0, Math.min(99, it.qty + d));
    render();
  }
  document.addEventListener('click', function (e) {
    var opt = e.target.closest('[data-tgsp-add]');
    if (opt) {
      add(opt.dataset.tgspCode, opt.dataset.name, +opt.dataset.price, 1);
      pInput.value = '';
      pList.innerHTML = '';
      return;
    }
    var btn = e.target.closest('.tgsp-row [data-d]');
    if (btn) add(btn.closest('.tgsp-row').dataset.code, '', 0, +btn.dataset.d);
  });
  pForm.addEventListener('change', render);
  pForm.querySelector('[data-tgsp-reset]').addEventListener('click', function () {
    basket = {}; order = [];
    pForm.querySelectorAll('input[name="pp"]').forEach(function (el) { el.checked = false; });
    render();
  });
  pInput.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    var first = pList.querySelector('[data-tgsp-add]');
    if (first) first.click();
  });
  live(pInput, pList, pInput.dataset.tgspFind, null, markOptions);
})();
